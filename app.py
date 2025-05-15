import streamlit as st
import pandas as pd
import altair as alt
import ast
import io

st.set_page_config(layout="wide")
st.title("Categorical Distribution Explorer")

# --- Source selector ---
data_source = st.sidebar.radio(
    "Select data source",
    ["Companies House", "FCA"]
)

# --- 1. Upload & Load company data ---
uploaded_file = st.file_uploader(
    f"Upload your {data_source} data (CSV or Excel)",
    type=["csv", "xls", "xlsx"]
)
if not uploaded_file:
    st.info("Please upload a CSV or Excel file to continue.")
    st.stop()

try:
    name = uploaded_file.name.lower()
    if name.endswith(".csv"):
        df = pd.read_csv(uploaded_file)
    else:
        xls = pd.ExcelFile(uploaded_file)
        sheet = st.selectbox("Select sheet", xls.sheet_names)
        df = pd.read_excel(xls, sheet_name=sheet)
except Exception as e:
    st.error(f"Could not read data: {e}")
    st.stop()

st.success(f"Loaded `{uploaded_file.name}`: {df.shape[0]} rows × {df.shape[1]} cols")

# --- For Companies House, load SIC lookup ---
sic_lookup = None
if data_source == "Companies House":
    try:
        sic_lookup = pd.read_csv("sic_codes.csv", dtype={"SIC_Code": str})
        sic_lookup["SIC_Code"] = sic_lookup["SIC_Code"].str.zfill(5)
    except Exception as e:
        st.error(f"Could not load SIC codes: {e}")
        st.stop()

# --- 2. Sidebar navigation ---
page = st.sidebar.radio("Go to", ["Distribution", "Data Table"])

# --- 3A. Distribution page ---
if page == "Distribution":
    st.header(f"Distribution View ({data_source})")

    # --- Define choices based on data source ---
    if data_source == "Companies House":
        choices = [
            "SIC Codes (unique)",
            "SIC Codes (original)",
            "Country",
            "Type",
            "Jurisdiction",
            "Company Status"
        ]
    else:
        # FCA: specific multi-entry columns and their unique variants + broad categories
        base_fields = [
            'investments_company_types', 'investments_investment_types', 'investments_limitations',
            'pensions_company_types', 'pensions_investment_types',
            'insurance_company_types', 'insurance_investment_types',
            'mortgages_company_types', 'mortgages_investment_types', 'mortgages_limitations',
            'credit_company_types', 'credit_investment_types'
        ]
        broad_fields = [
           'ar'
        ]
        choices = []
        # add list-based fields and their unique variants
        for col in base_fields:
            if col in df.columns:
                choices.append(col)
                choices.append(f"{col} (unique)")
        # add broad category selectors
        for bf in broad_fields:
            choices.append(bf)

    choice = st.selectbox("Choose a distribution to view", choices)

    # --- Build counts series ---
    if data_source == "Companies House":
        if choice == "SIC Codes (unique)":
            lists = df["sic_codes"].dropna().astype(str).apply(ast.literal_eval)
            exploded = lists.explode().astype(str).str.zfill(5)
            counts = exploded.value_counts()
        elif choice == "SIC Codes (original)":
            codes = df["sic_codes"].fillna("<NA>").astype(str)
            counts = codes.value_counts()
        else:
            col_map = {
                "Country": "registered_office_address.country",
                "Type": "type",
                "Jurisdiction": "jurisdiction",
                "Company Status": "company_status"
            }
            col = col_map[choice]
            counts = df[col].fillna("<NA>").astype(str).value_counts()
    else:
        # FCA logic: handle unique vs original with semicolon separation
        if choice.endswith(" (unique)"):
            base = choice.replace(" (unique)", "")
            lists = (
                df[base]
                .dropna()
                .astype(str)
                .str.split(";")
                .apply(lambda lst: [s.strip() for s in lst])
            )
            exploded = lists.explode().astype(str)
            counts = exploded.value_counts()
        else:
            counts = df[choice].fillna("<NA>").astype(str).value_counts()

    # --- Top-N slider & slice ---
    max_vals = len(counts)
    top_n = st.slider("Show top N categories", 1, max_vals, min(10, max_vals))
    top_counts = counts.iloc[:top_n]

    # --- Build plot_df ---
    plot_df = top_counts.reset_index()
    plot_df.columns = ["category", "count"]
    plot_df["percent"] = (plot_df["count"] / plot_df["count"].sum() * 100).round(2)

    # --- Merge SIC descriptions if needed ---
    if data_source == "Companies House" and choice.startswith("SIC Codes"):
        plot_df = (
            plot_df
            .merge(
                sic_lookup,
                left_on="category",
                right_on="SIC_Code",
                how="left"
            )
            .drop(columns=["SIC_Code"])
            .rename(columns={"Description": "SIC_Description"})
        )

    if plot_df.empty:
        st.warning("No data to display for this selection.")
        st.stop()

    # --- Display table and chart ---
    st.subheader(f"Top {top_n} of `{choice}`")
    st.dataframe(plot_df)

    mean_count = plot_df["count"].mean()
    chart = (
        alt.Chart(plot_df)
           .mark_bar()
           .encode(
               y=alt.Y("category:N", sort=alt.SortField("count", order="descending"), title=choice),
               x=alt.X("count:Q", title="Count"),
               color=alt.condition(f"datum.count >= {mean_count}", alt.value("#2171b5"), alt.value("#deebf7")),
               tooltip=[alt.Tooltip("category:N", title=choice), alt.Tooltip("count:Q", title="Count"), alt.Tooltip("percent:Q", title="Percentage (%)")]
           )
           .properties(width=800, height=400)
           .configure_axis(labelFontSize=12, titleFontSize=14)
           .configure_view(strokeOpacity=0)
    )
    st.altair_chart(chart, use_container_width=True)

    # --- Download results ---
    csv = plot_df.to_csv(index=False)
    st.download_button(
        "Download CSV",
        data=csv,
        file_name=f"{data_source}_{choice.replace(' ', '_')}.csv",
        mime="text/csv"
    )

# --- 3B. Data Table view ---
else:
    st.header("Data Table View")
    st.write(f"Dataset: {df.shape[0]} rows × {df.shape[1]} columns")

    cols_to_show = st.multiselect("Select columns to display", options=df.columns.tolist(), default=df.columns.tolist())
    if not cols_to_show:
        st.warning("Please select at least one column.")
        st.stop()

    search = st.text_input("Filter rows by search term (visible columns)")
    sort_col = st.selectbox("Sort by column", options=[""] + cols_to_show, format_func=lambda x: "- none -" if x == "" else x)
    ascending = st.radio("Sort order", ["Ascending", "Descending"]) == "Ascending"

    df_view = df[cols_to_show].copy()
    if search:
        df_view = df_view[df_view.astype(str).apply(lambda row: row.str.contains(search, case=False, na=False).any(), axis=1)]
        st.write(f"{len(df_view)} rows match '{search}'")

    if sort_col:
        df_view = df_view.sort_values(by=sort_col, ascending=ascending, na_position="last")

    st.dataframe(df_view, use_container_width=True)

    fmt = st.radio("Download format", ["CSV", "Excel"])
    if fmt == "CSV":
        out = df_view.to_csv(index=False).encode("utf-8")
        st.download_button("Download CSV", data=out, file_name="data_table.csv", mime="text/csv")
    else:
        buffer = io.BytesIO()
        with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
            df_view.to_excel(writer, index=False, sheet_name="Sheet1")
        st.download_button("Download Excel", data=buffer.getvalue(), file_name="data_table.xlsx", mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")