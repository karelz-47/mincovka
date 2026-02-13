import streamlit as st
import pandas as pd
from io import BytesIO
import re

# ---------------------------
# Helpers
# ---------------------------

DENOMS_EUR = [100, 50, 20, 10, 5, 2, 1, 0.50, 0.20, 0.10, 0.05, 0.02, 0.01]

def euro_label(n: float) -> str:
    # Pretty labels in Slovak, keep € sign
    if n >= 1:
        return f"{int(n)} €"
    # coins
    cents = int(round(n * 100))
    return f"{cents} c"

DENOM_LABELS = [euro_label(d) for d in DENOMS_EUR]

def to_cents(amount_eur: float) -> int:
    # robust enough for UI numbers; Streamlit gives float
    return int(round(amount_eur * 100))

def cents_to_eur(cents: int) -> float:
    return cents / 100.0

def format_eur_sk(amount: float) -> str:
    # Display with comma as decimal separator (Slovak style)
    return f"{amount:.2f}".replace(".", ",")

def parse_amount_sk(raw: str) -> tuple[bool, float, str]:
    """
    Accepts:
      - "123"
      - "123,4"
      - "123,45"
      - "123.45" (also OK)
      - optional spaces
    Rejects negatives and non-numeric.
    Returns: (ok, value_float, error_message)
    """
    if raw is None:
        return True, 0.0, ""
    s = raw.strip().replace(" ", "")
    if s == "":
        return True, 0.0, ""

    # Allow digits with optional decimal part using , or .
    if not re.fullmatch(r"\d+([,.]\d{0,2})?", s):
        return False, 0.0, "Zadajte číslo vo formáte napr. 12,34 (max. 2 desatinné miesta)."

    s = s.replace(",", ".")
    try:
        val = float(s)
    except ValueError:
        return False, 0.0, "Neplatná suma."

    if val < 0:
        return False, 0.0, "Suma nemôže byť záporná."

    # Round to cents
    val = round(val + 1e-12, 2)
    return True, val, ""

def idx_to_person_code(idx: int) -> str:
    """
    0 -> A, 1 -> B, ... 25 -> Z, 26 -> AA, ...
    """
    idx += 1
    s = ""
    while idx > 0:
        idx, rem = divmod(idx - 1, 26)
        s = chr(65 + rem) + s
    return s

def compute_breakdown(amount_cents: int) -> dict:
    """
    Returns dict {denom_label: count, ...}, greedy from highest to lowest.
    """
    remainder = amount_cents
    out = {}
    for denom in DENOMS_EUR:
        denom_cents = int(round(denom * 100))
        cnt = remainder // denom_cents
        out[euro_label(denom)] = int(cnt)
        remainder -= int(cnt) * denom_cents
    return out

def breakdown_value_cents(breakdown: dict) -> int:
    total = 0
    for denom, cnt in breakdown.items():
        # denom is like "50 €" or "20 c"
        if "€" in denom:
            v = int(denom.replace("€", "").strip())
            total += v * 100 * cnt
        else:
            v = int(denom.replace("c", "").strip())
            total += v * cnt
    return total

def build_excel(per_person_df: pd.DataFrame, summary_df: pd.DataFrame) -> bytes:
    bio = BytesIO()
    with pd.ExcelWriter(bio, engine="openpyxl") as writer:
        per_person_df.to_excel(writer, index=False, sheet_name="Osoby")
        summary_df.to_excel(writer, index=False, sheet_name="Súhrn")
    return bio.getvalue()

# ---------------------------
# Session state init
# ---------------------------

if "persons" not in st.session_state:
    st.session_state.persons = []  # list of dicts: {"id": int, "code": str, "amount": float}
if "next_id" not in st.session_state:
    st.session_state.next_id = 1
if "calculated" not in st.session_state:
    st.session_state.calculated = False
if "per_person_df" not in st.session_state:
    st.session_state.per_person_df = None
if "summary_df" not in st.session_state:
    st.session_state.summary_df = None

# ---------------------------
# UI
# ---------------------------

st.set_page_config(page_title="Mincovka", layout="wide")
st.title("Rozpis bankoviek a mincí (EUR)")

st.caption(
    "Pridajte osoby (bez mien) a sumy v EUR. Po kliknutí na „Vypočítať“ sa suma rozloží "
    "na bankovky/mince s prioritou najvyššej hodnoty (max. bankovka 100 €). "
    "Desatinný oddeľovač používajte čiarku (napr. 12,34)."
)

# Controls
c1, c2, c3 = st.columns([1, 1, 3])
with c1:
    if st.button("➕ Pridať osobu", use_container_width=True):
        if len(st.session_state.persons) < 100:
            code = idx_to_person_code(len(st.session_state.persons))
            st.session_state.persons.append({"id": st.session_state.next_id, "code": code, "amount": 0.0})
            st.session_state.next_id += 1
            st.session_state.calculated = False
        else:
            st.warning("Limit je 100 osôb.")

with c2:
    if st.button("➕➕ Pridať 10 osôb", use_container_width=True):
        remaining = 100 - len(st.session_state.persons)
        add_n = min(10, remaining)
        if add_n <= 0:
            st.warning("Limit je 100 osôb.")
        else:
            for _ in range(add_n):
                code = idx_to_person_code(len(st.session_state.persons))
                st.session_state.persons.append({"id": st.session_state.next_id, "code": code, "amount": 0.0})
                st.session_state.next_id += 1
            st.session_state.calculated = False

with c3:
    st.write("")

st.divider()

if len(st.session_state.persons) == 0:
    st.info("Zatiaľ nemáte pridanú žiadnu osobu. Kliknite na „Pridať osobu“.")
else:
    st.subheader("Osoby a sumy")

    # Header row
    h1, h2, h3, h4 = st.columns([1, 3, 2, 1])
    h1.markdown("**Osoba**")
    h2.markdown("**Suma (EUR)**")
    h3.markdown("**Poznámka**")
    h4.markdown("**Akcie**")

    to_delete_ids = set()
    validation_errors = []

    for p in st.session_state.persons:
        col1, col2, col3, col4 = st.columns([1, 3, 2, 1])

        col1.write(p["code"])

        # Text input with comma decimals (displayed with comma)
        default_txt = format_eur_sk(float(p["amount"]))
        raw = col2.text_input(
            label=f"Suma pre {p['code']}",
            value=default_txt,
            key=f"amt_txt_{p['id']}",
            label_visibility="collapsed",
            placeholder="napr. 12,34",
        )

        ok, val, err = parse_amount_sk(raw)
        if ok:
            if val != p["amount"]:
                p["amount"] = val
                st.session_state.calculated = False
        else:
            validation_errors.append((p["code"], err))
            col2.error(err)

        col3.caption("Formát: 12,34 (max. 2 desatinné miesta).")

        if col4.button("🗑️ Zmazať", key=f"del_{p['id']}", use_container_width=True):
            to_delete_ids.add(p["id"])

    if to_delete_ids:
        st.session_state.persons = [p for p in st.session_state.persons if p["id"] not in to_delete_ids]
        st.session_state.calculated = False
        st.rerun()

    st.divider()

    # Calculate
    calc_col1, calc_col2, calc_col3 = st.columns([1, 1, 3])
    with calc_col1:
        do_calc = st.button(
            "🧮 Vypočítať",
            use_container_width=True,
            disabled=(len(validation_errors) > 0),
        )
    with calc_col2:
        st.write("")
    with calc_col3:
        if validation_errors:
            st.warning("Opravte chyby v sumách vyššie — potom bude možné vypočítať.")
        else:
            st.write("")

    if do_calc:
        rows = []
        summary_counts = {lbl: 0 for lbl in DENOM_LABELS}
        summary_total_cents = 0

        for p in st.session_state.persons:
            amt_cents = to_cents(p["amount"])
            breakdown = compute_breakdown(amt_cents)
            computed_cents = breakdown_value_cents(breakdown)

            row = {"Osoba": p["code"], "Suma (EUR)": round(cents_to_eur(amt_cents), 2)}
            for lbl in DENOM_LABELS:
                row[lbl] = breakdown.get(lbl, 0)
                summary_counts[lbl] += breakdown.get(lbl, 0)

            row["Kontrola (EUR)"] = round(cents_to_eur(computed_cents), 2)
            row["Rozdiel (EUR)"] = round(cents_to_eur(amt_cents - computed_cents), 2)
            rows.append(row)

            summary_total_cents += computed_cents

        per_person_df = pd.DataFrame(rows)

        # Summary table: counts and value per denom
        sum_rows = []
        for denom, lbl in zip(DENOMS_EUR, DENOM_LABELS):
            cnt = summary_counts[lbl]
            denom_cents = int(round(denom * 100))
            value_cents = cnt * denom_cents
            sum_rows.append({
                "Nominál": lbl,
                "Počet kusov": cnt,
                "Suma (EUR)": round(cents_to_eur(value_cents), 2),
            })

        summary_df = pd.DataFrame(sum_rows)
        summary_df = pd.concat(
            [summary_df, pd.DataFrame([{
                "Nominál": "SPOLU",
                "Počet kusov": int(summary_df["Počet kusov"].sum()),
                "Suma (EUR)": round(cents_to_eur(summary_total_cents), 2),
            }])],
            ignore_index=True
        )

        st.session_state.per_person_df = per_person_df
        st.session_state.summary_df = summary_df
        st.session_state.calculated = True

    # Results + Download
    if st.session_state.calculated and st.session_state.per_person_df is not None:
        st.subheader("Výsledky — rozpis na bankovky a mince")

        st.markdown("**Rozpis po osobách**")
        st.dataframe(st.session_state.per_person_df, use_container_width=True, hide_index=True)

        st.markdown("**Súhrn (spolu za všetky osoby)**")
        st.dataframe(st.session_state.summary_df, use_container_width=True, hide_index=True)

        st.divider()

        excel_bytes = build_excel(st.session_state.per_person_df, st.session_state.summary_df)

        st.download_button(
            label="⬇️ Stiahnuť report (Excel .xlsx)",
            data=excel_bytes,
            file_name="rozpis_bankoviek_a_minci.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            use_container_width=True,
        )

# Footer
st.caption("Pozn.: Rozpis používa nominály EUR: 100, 50, 20, 10, 5, 2, 1, 0.50, 0.20, 0.10, 0.05, 0.02, 0.01.")
