# app.py
import streamlit as st
import pandas as pd
import altair as alt

st.set_page_config(layout="wide")
st.title("Categorical Distribution Explorer")

# --- Upload & Load ---
uploaded_file = st.file_uploader(
    "Upload your companies data (CSV or Excel)", 
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

# --- Sidebar page selector ---
page = st.sidebar.radio("Go to", ["Distribution", "Data Table"])


if page == "Distribution":
    st.header("Distribution View")

    # Column selection
    col = st.selectbox("Choose a categorical column", df.columns.tolist())
    unique_vals = df[col].nunique(dropna=False)
    top_n = st.slider(
        "Show top N categories (max = all)",
        1, unique_vals, value=min(10, unique_vals)
    )

    # Compute top-N
    counts = df[col].fillna("<NA>").astype(str).value_counts()
    top_counts = counts.iloc[:top_n]
    plot_df = (
        top_counts
        .reset_index()
        .rename(columns={"index": col, 0: "count"})
    )
    plot_df["percent"] = (plot_df["count"] / plot_df["count"].sum() * 100).round(2)

    # Show table
    st.subheader(f"Top {top_n} categories for `{col}`")
    st.dataframe(plot_df)

    # Compute mean for hue
    mean_count = plot_df["count"].mean()

    # Bar chart + heatmap hue
    st.subheader("Distribution Chart")
    chart = (
        alt.Chart(plot_df)
        .mark_bar()
        .encode(
            y=alt.Y(
                f"{col}:N",
                sort=alt.SortField(field="count", order="descending"),
                title=col
            ),
            x=alt.X("count:Q", title="Count"),
            color=alt.condition(
                f"datum.count >= {mean_count}",
                alt.value("#2171b5"),   # darker blue (above-mean)
                alt.value("#deebf7")    # very light blue (below-mean)
            ),
            tooltip=[
                alt.Tooltip("count:Q", title="Count"),
                alt.Tooltip("percent:Q", title="Percentage (%)")
            ],
        )
        .properties(width=800, height=400)
        .configure_axis(labelFontSize=12, titleFontSize=14)
        .configure_view(strokeOpacity=0)
    )
    st.altair_chart(chart, use_container_width=True)

    # Download
    csv = plot_df.to_csv(index=False)
    st.download_button(
        "Download counts + % as CSV",
        data=csv,
        file_name=f"{col}_value_counts.csv",
        mime="text/csv"
    )
else:
    st.header("Data Table View")
    st.write(f"Dataset: {df.shape[0]} rows × {df.shape[1]} columns")

    # 1) Column selection
    cols_to_show = st.multiselect(
        "Select columns to display",
        options=df.columns.tolist(),
        default=df.columns.tolist()
    )
    if not cols_to_show:
        st.warning("Pick at least one column to display.")
        st.stop()

    # 2) Search filter (across selected columns)
    search = st.text_input("Filter rows by search term (applies to visible columns)")
    
    # 3) Sort controls
    sort_col = st.selectbox(
        "Sort by column",
        options=[""] + cols_to_show,
        format_func=lambda x: "— none —" if x=="" else x
    )
    ascending = st.radio("Sort order", ["Ascending", "Descending"]) == "Ascending"

    # Apply filtering
    df_view = df[cols_to_show].copy()
    if search:
        mask = df_view.astype(str).apply(
            lambda row: row.str.contains(search, case=False, na=False).any(),
            axis=1
        )
        df_view = df_view[mask]
        st.write(f"{len(df_view)} rows match “{search}”")

    # Apply sorting
    if sort_col:
        df_view = df_view.sort_values(by=sort_col, ascending=ascending, na_position="last")

    # Show the DataFrame
    st.dataframe(df_view, use_container_width=True)

    # Download filtered/sorted subset
    fmt = st.radio("Download format", ["CSV", "Excel"])
    if fmt == "CSV":
        out = df_view.to_csv(index=False).encode("utf-8")
        st.download_button(
            "Download as CSV", 
            data=out, 
            file_name="data_table.csv", 
            mime="text/csv"
        )
    else:
        # Write Excel to a buffer
        import io
        buffer = io.BytesIO()
        with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
            df_view.to_excel(writer, index=False, sheet_name="Sheet1")
        st.download_button(
            "Download as Excel", 
            data=buffer.getvalue(), 
            file_name="data_table.xlsx", 
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
