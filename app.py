import logging
import os

import pandas as pd
import streamlit as st
import matplotlib.pyplot as plt
from sqlalchemy import create_engine, MetaData, text
from sqlalchemy.orm import sessionmaker
from pydantic import BaseModel
from dotenv import load_dotenv
from openai import AzureOpenAI

# ─── Logging ────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s"
)

# ─── Streamlit Config ───────────────────────────────────────
st.set_page_config(page_title="Database Explorer", layout="wide")
load_dotenv()

# ─── Session State Defaults ─────────────────────────────────
if "connected" not in st.session_state:
    st.session_state.connected = False
    st.session_state.db_url = ""
    st.session_state.engine = None
    st.session_state.sessionmaker = None
    st.session_state.tables = []
    st.session_state.client = None
    st.session_state.page = "Connect to Database"

# ─── Sidebar Navigation ─────────────────────────────────────
with st.sidebar:
    st.header("Navigation")
    choice = st.radio(
        "Select a page:",
        ["Connect to Database", "Query Database", "Healthcheck"]
    )
    st.session_state.page = choice

    if not st.session_state.connected and choice != "Connect to Database":
        st.warning("Please connect to a database first.")

# ─── Helper: Run SQL and return DataFrame ────────────────────
def run_sql(sql: str) -> pd.DataFrame:
    with st.session_state.sessionmaker() as session:
        result = session.execute(text(sql))
        rows, cols = result.fetchall(), result.keys()
    return pd.DataFrame(rows, columns=cols)

# ─── Page 1: Connect to Database ────────────────────────────
if st.session_state.page == "Connect to Database":
    st.header("Connect to Database")
    database = st.text_input("Database")
    user     = st.text_input("Username")
    password = st.text_input("Password", type="password")

    st.markdown("---")
    st.header("Azure OpenAI Settings")
    azure_endpoint   = st.text_input("Azure OpenAI Endpoint")
    azure_api_key    = st.text_input("Azure OpenAI API Key", type="password")
    azure_api_version= "2024-12-01-preview"

    if st.button("Connect"):
        # 1) build DB session
        db_url = f"postgresql+psycopg2://{user}:{password}@4.tcp.eu.ngrok.io:15796/{database}"
        try:
            engine = create_engine(db_url, future=True)
            Session = sessionmaker(bind=engine, autoflush=False, autocommit=False)

            meta = MetaData()
            meta.reflect(bind=engine)
            tables = [t.name for t in meta.sorted_tables]

            # 2) build AzureOpenAI client
            client = AzureOpenAI(
                api_key=azure_api_key,
                api_version=azure_api_version,
                azure_endpoint=azure_endpoint
            )

            # 3) store into session_state
            st.session_state.connected    = True
            st.session_state.db_url        = db_url
            st.session_state.engine        = engine
            st.session_state.sessionmaker  = Session
            st.session_state.tables        = tables
            st.session_state.client        = client

            st.success("Connected to database and Azure OpenAI.")
            st.subheader("Available Tables")
            st.write(tables)

        except Exception as e:
            logging.exception("Connection failed")
            st.error(f"Connection failed: {e}")

# ─── Guard ──────────────────────────────────────────────────
elif not st.session_state.connected:
    st.stop()

# ─── Page 2: Query Database ─────────────────────────────────
elif st.session_state.page == "Query Database":
    st.header("Query Database in Natural Language")

    class SQLResponse(BaseModel):
        sql: str

    # ─── Tables to Exclude from Schema View ─────────────────────
    SCHEMA_BLACKLIST = {'shared_leads_individual_incomplete'}

    def get_schema_str() -> str:
        """
        Reflects the database schema and returns a textual
        description of tables *except* those in SCHEMA_BLACKLIST.
        """
        meta = MetaData()
        meta.reflect(bind=st.session_state.engine)

        lines = []
        for table in meta.sorted_tables:
            if table.name in SCHEMA_BLACKLIST:
                continue
            cols = ", ".join(f"{c.name} {c.type}" for c in table.columns)
            lines.append(f"{table.name}({cols})")

        return "\n".join(lines)

    def generate_sql(nl: str) -> str:
        prompt = (
            "You are an expert SQL assistant. Convert the user's instruction "
            "into a single PostgreSQL SELECT query. Output only the SQL.\n\n"
            f"Schema:\n{get_schema_str()}\n\n"
            f"Instruction: {nl}"
        )
        resp = st.session_state.client.beta.chat.completions.parse(
            model="gpt-4o",
            messages=[{"role": "system", "content": prompt}],
            response_format=SQLResponse,
            temperature=0
        )
        return resp.choices[0].message.parsed.sql.strip()

    nl_query = st.text_input(
        "Enter your request:",
        "Show me list of all individuals with email verified"
    )
    if st.button("Run Query"):
        try:
            sql = generate_sql(nl_query)
            df  = run_sql(sql)
            st.subheader("Generated SQL")
            st.code(sql, language="sql")
            st.subheader("Results")
            st.dataframe(df)
        except Exception as e:
            logging.exception("Query failed")
            st.error(f"Error: {e}")

# ─── Page 3: Healthcheck ─────────────────────────────────────
else:
    st.header("Healthcheck Metrics")

    def healthcheck_metrics() -> dict[str, dict[str, int]]:
        m = {}

        df1 = run_sql("""
            SELECT
              SUM((web_domain IS NOT NULL)::int) AS present,
              SUM((web_domain IS NULL)::int)       AS missing
            FROM shared_leads_company
        """)
        m["Web Domain"] = df1.loc[0].to_dict()

        df2 = run_sql("""
            WITH groups AS (
              SELECT
                name,
                COUNT(DISTINCT regulatory_number) AS rn,
                COUNT(DISTINCT company_number)    AS cn
              FROM shared_leads_company
              GROUP BY name
            )
            SELECT
              SUM((rn>1 OR cn>1)::int) AS duplicates,
              SUM((rn<=1 AND cn<=1)::int) AS unique
            FROM groups
        """)
        m["Duplicate Names"] = df2.loc[0].to_dict()

        df3 = run_sql("""
            SELECT
              SUM((email IS NULL)::int) AS no_email,
              SUM((email_verified IS TRUE)::int) AS verified,
              SUM((email IS NOT NULL AND (email_verified=FALSE OR email_verified IS NULL))::int)
                  AS unverified
            FROM shared_leads_individual
        """)
        m["Email Verification"] = df3.loc[0].to_dict()

        total = run_sql("SELECT COUNT(*) AS cnt FROM shared_leads_company").iloc[0,0]
        lead_df = run_sql("""
            SELECT
              company_id,
              MAX((email_verified IS TRUE)::int) AS any_verified
            FROM shared_leads_individual
            GROUP BY company_id
        """)
        with_leads = set(lead_df['company_id'].dropna())
        verified   = set(lead_df.loc[lead_df['any_verified']==1,'company_id'])
        m["Leads per Company"] = {
            "no_leads":        total - len(with_leads),
            "unverified_only": len(with_leads - verified),
            "any_verified":    len(verified)
        }
        return m

    metrics   = healthcheck_metrics()
    chart_keys= ["Web Domain","Duplicate Names","Email Verification","Leads per Company"]

    for ax, key in zip(axes, chart_keys):
        data = metrics[key]
        total = sum(data.values())
    
        # Define colors based on key and labels
        color_map = []
        for label in data.keys():
            label_lower = label.lower()
            if "missing" in label_lower or "no" in label_lower:
                color_map.append("red")
            elif "unverified" in label_lower:
                color_map.append("orange")
            elif "verified" in label_lower or "unique" in label_lower or "yes" in label_lower or "present" in label_lower or "any" in label_lower:
                color_map.append("green")
            else:
                color_map.append("gray")
    
        wedges, texts, autotexts = ax.pie(
            data.values(),
            startangle=90,
            autopct=lambda p: f"{p:.1f}%" if p > 0 else "",
            pctdistance=0.7,
            colors=color_map,
            wedgeprops={"linewidth": 0.5, "edgecolor": "white"},
            textprops={"fontsize": 8, "color": "white"}
        )
    
        ax.set_title(f"{key}\n(Total: {total})", fontsize=10)
        ax.axis("equal")
    
        labels = [f"{k} ({v})" for k, v in data.items()]
        ax.legend(
            wedges, labels,
            loc="lower center",
            bbox_to_anchor=(0.5, -0.15),
            fontsize=8, ncol=len(data)
        )

    fig.tight_layout(pad=3, rect=[0,0.05,1,1])
    st.pyplot(fig)
