import psycopg
import streamlit as st


@st.cache_resource
def get_conn():

    host = st.secrets["SUPABASE_DB_HOST"]
    port = st.secrets.get("SUPABASE_DB_PORT", "5432")
    dbname = st.secrets.get("SUPABASE_DB_NAME", "postgres")
    user = st.secrets["SUPABASE_DB_USER"]
    password = st.secrets["SUPABASE_DB_PASSWORD"]
    sslmode = st.secrets.get("SUPABASE_DB_SSLMODE", "require")

    conn_str = (
        f"host={host} "
        f"port={port} "
        f"dbname={dbname} "
        f"user={user} "
        f"password={password} "
        f"sslmode={sslmode}"
    )

    conn = psycopg.connect(
        conn_str,
        autocommit=True,
    )

    # Important for Supabase pooler:
    # never automatically create server-side prepared statements.
    conn.prepare_threshold = None

    return conn