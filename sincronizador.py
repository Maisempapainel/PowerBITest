import os
import datetime
import requests
import psycopg2
from psycopg2.extras import execute_values

CLIENT_ID = os.environ.get("CONTAAZUL_CLIENT_ID")
CLIENT_SECRET = os.environ.get("CONTAAZUL_CLIENT_SECRET")
SUPABASE_DATABASE_URI = os.environ.get("SUPABASE_DATABASE_URI")

def get_db_connection():
    return psycopg2.connect(SUPABASE_DATABASE_URI)

def load_stored_credentials():
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT access_token, refresh_token, expires_at FROM contaazul_credentials ORDER BY id DESC LIMIT 1;")
    row = cur.fetchone()
    cur.close()
    conn.close()
    return row

def save_credentials(access_token, refresh_token, expires_in):
    conn = get_db_connection()
    cur = conn.cursor()
    expires_at = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(seconds=expires_in)
    cur.execute(
        "INSERT INTO contaazul_credentials (access_token, refresh_token, expires_at) VALUES (%s, %s, %s);",
        (access_token, refresh_token, expires_at)
    )
    conn.commit()
    cur.close()
    conn.close()
    print("[Postgres] Novas chaves armazenadas no Supabase!")

def refresh_access_token(old_refresh_token):
    print("[OAuth] Solicitando renovação automática de token expirado...")
    url = "https://api.contaazul.com/oauth2/token"
    payload = {"grant_type": "refresh_token", "refresh_token": old_refresh_token}
    response = requests.post(url, json=payload, auth=(CLIENT_ID, CLIENT_SECRET))
    
    if response.status_code == 200:
        data = response.json()
        save_credentials(data["access_token"], data["refresh_token"], data["expires_in"])
        return data["access_token"]
    else:
        raise ValueError("Token de renovação falhou. Necessário aprovar manualmente na aba Sincronizador.")

def get_active_token():
    creds = load_stored_credentials()
    if not creds:
        raise ValueError("Nenhum token inicial encontrado no Postgres Supabase. Execute a primeira autorização física.")
    access_token, refresh_token, expires_at = creds
    now = datetime.datetime.now(datetime.timezone.utc)
    if expires_at - datetime.timedelta(minutes=5) < now:
        return refresh_access_token(refresh_token)
    return access_token

def fetch_financial_entries_from_contaazul(api_token):
    headers = {"Authorization": f"Bearer {api_token}"}
    mapped_transactions = []
    
    # Busca contas a receber
    res_rec = requests.get("https://api.contaazul.com/v1/receivables", headers=headers)
    if res_rec.status_code == 200:
        for item in res_rec.json().get("items", []):
            due_date = item.get("due_date", "")
            status = "PAGO" if item.get("status") in ["RECEIVED", "PAID"] else "EM_ABERTO"
            if status == "EM_ABERTO" and due_date and datetime.datetime.strptime(due_date, "%Y-%m-%d").date() < datetime.date.today():
                status = "ATRASADO"
            mapped_transactions.append((
                item["id"], "RECEBER", item.get("customer", {}).get("name", "Cliente Consumidor"),
                item.get("emission_date", datetime.date.today().isoformat()), due_date,
                item.get("category", {}).get("name", "Receitas Operacionais"),
                item.get("cost_center", {}).get("name", "Comercial / Vendas"),
                item.get("description", "Faturamento Comercial"), float(item.get("value", 0)), status, item.get("notes", "")
            ))
            
    # Busca contas a pagar
    res_pay = requests.get("https://api.contaazul.com/v1/payables", headers=headers)
    if res_pay.status_code == 200:
        for item in res_pay.json().get("items", []):
            due_date = item.get("due_date", "")
            status = "PAGO" if item.get("status") in ["PAID", "RECEIVED"] else "EM_ABERTO"
            if status == "EM_ABERTO" and due_date and datetime.datetime.strptime(due_date, "%Y-%m-%d").date() < datetime.date.today():
                status = "ATRASADO"
            mapped_transactions.append((
                item["id"], "PAGAR", item.get("supplier", {}).get("name", "Fornecedor Padrão"),
                item.get("emission_date", datetime.date.today().isoformat()), due_date,
                item.get("category", {}).get("name", "Despesas Operacionais"),
                item.get("cost_center", {}).get("name", "Administrativo & Operações"),
                item.get("description", "Material / Custo"), float(item.get("value", 0)), status, item.get("notes", "")
            ))
    return mapped_transactions

def upsert_into_supabase(transactions):
    if not transactions:
        print("Nenhum dado novo.")
        return
    query = """
    INSERT INTO lancamentos_financeiros (
        id, tipo, fornecedor_cliente, data_competencia, data_vencimento,
        categoria, centro_de_custo, descricao, valor, status, observacao
    ) VALUES %s
    ON CONFLICT (id) DO UPDATE SET
        tipo = EXCLUDED.tipo, fornecedor_cliente = EXCLUDED.fornecedor_cliente,
        data_competencia = EXCLUDED.data_competencia, data_vencimento = EXCLUDED.data_vencimento,
        categoria = EXCLUDED.categoria, centro_de_custo = EXCLUDED.centro_de_custo,
        descricao = EXCLUDED.descricao, valor = EXCLUDED.valor, status = EXCLUDED.status,
        observacao = EXCLUDED.observacao, atualizado_em = NOW();
    """
    conn = get_db_connection()
    cur = conn.cursor()
    try:
        execute_values(cur, query, transactions)
        conn.commit()
        print(f"[Sucesso Supabase] Sincronizados {len(transactions)} lançamentos financeira corporativa.")
    except Exception as e:
        conn.rollback()
        print(e)
    finally:
        cur.close()
        conn.close()

if __name__ == "__main__":
    try:
        token = get_active_token()
        data = fetch_financial_entries_from_contaazul(token)
        upsert_into_supabase(data)
    except Exception as e:
        print(f"Erro: {e}")
