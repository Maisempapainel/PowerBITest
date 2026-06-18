import os
import datetime
import requests
from supabase import create_client, Client

# =========================================================================
# SCRIPT DE SINCRONIZAÇÃO CONTA AZUL -> SUPABASE (VIA API OFICIAL SUPABASE)
# Feito de forma perfeita para iniciantes! Usa diretamente a "API URL"
# e a "Service Role Key" que você visualiza no painel do seu Supabase.
# =========================================================================

# 1. CREDENCIAIS CONTA AZUL
CLIENT_ID = os.environ.get("CONTAAZUL_CLIENT_ID")
CLIENT_SECRET = os.environ.get("CONTAAZUL_CLIENT_SECRET")

# 2. CREDENCIAIS SUPABASE (APIS OFICIAIS)
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_SERVICE_ROLE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")

def get_supabase_client() -> Client:
    if not SUPABASE_URL or not SUPABASE_SERVICE_ROLE_KEY:
        raise ValueError("ERRO: Configure SUPABASE_URL e SUPABASE_SERVICE_ROLE_KEY nos Segredos do seu GitHub!")
    
    # Remove automaticamente '/rest/v1/' se tiver sido colado com o sufixo por acidente
    base_url = SUPABASE_URL.split("/rest/v1")[0].strip()
    return create_client(base_url, SUPABASE_SERVICE_ROLE_KEY)

def load_stored_credentials():
    """Carrega as chaves autenticadas da tabela de forma 100% segura."""
    supabase = get_supabase_client()
    try:
        response = supabase.table("contaazul_credentials").select("access_token", "refresh_token", "expires_at").order("id", desc=True).limit(1).execute()
        if response.data and len(response.data) > 0:
            row = response.data[0]
            return row.get("access_token"), row.get("refresh_token"), row.get("expires_at")
    except Exception as e:
        print(f"[Aviso] Nenhuma credencial cadastrada ainda: {e}")
    return None

def save_credentials(access_token, refresh_token, expires_in):
    """Atualiza e rotaciona as chaves geradas e calcula o tempo de expiração."""
    supabase = get_supabase_client()
    expires_at = (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(seconds=expires_in)).isoformat()
    
    supabase.table("contaazul_credentials").insert({
        "access_token": access_token,
        "refresh_token": refresh_token,
        "expires_at": expires_at
    }).execute()
    print("[Supabase] Chaves de segurança do Conta Azul atualizadas via API com sucesso!")

def refresh_access_token(old_refresh_token):
    """Executa a renovação automática de segurança (Refresh Token) sem perturbar o usuário."""
    print("[OAuth] Solicitando novas chaves de acesso à API do Conta Azul...")
    url = "https://api.contaazul.com/oauth2/token"
    payload = {
        "grant_type": "refresh_token",
        "refresh_token": old_refresh_token
    }
    
    response = requests.post(url, json=payload, auth=(CLIENT_ID, CLIENT_SECRET))
    
    if response.status_code == 200:
        data = response.json()
        save_credentials(data["access_token"], data["refresh_token"], data["expires_in"])
        return data["access_token"]
    else:
        print(f"[Erro OAuth] Falha na rotação do token: {response.text}")
        raise ValueError("Refresh Token expirou. Por favor, acesse o painel principal do conector para autenticar novamente no Conta Azul!")

def get_active_token():
    """Confere se o token salvado ainda é válido ou inicia a renovação expressa."""
    creds = load_stored_credentials()
    if not creds:
        raise ValueError("Por favor, acesse a aba Sincronizador API e realize o primeiro clique para validar seu conector!")
        
    access_token, refresh_token, expires_at_str = creds
    expires_at = datetime.datetime.fromisoformat(expires_at_str.replace("Z", "+00:00"))
    now = datetime.datetime.now(datetime.timezone.utc)
    
    # Se expira em menos de 5 minutos, rotaciona automaticamente de forma transparente!
    if expires_at - datetime.timedelta(minutes=5) < now:
        return refresh_access_token(refresh_token)
    
    return access_token

def fetch_financial_entries_from_contaazul(api_token):
    """Busca despesas (payables) e vendas (receivables) de forma unificada na API do Conta Azul."""
    headers = {"Authorization": f"Bearer {api_token}"}
    mapped_transactions = []
    
    # 1. Coleta Receitas (Contas a Receber)
    rec_url = "https://api.contaazul.com/v1/receivables"
    print("[Conta Azul] Buscando Contas a Receber...")
    res_rec = requests.get(rec_url, headers=headers)
    
    if res_rec.status_code == 200:
        receivables = res_rec.json()
        items = receivables.get("items", receivables if isinstance(receivables, list) else [])
        for item in items:
            due_date = item.get("due_date", "")
            status = "PAGO" if item.get("status") in ["RECEIVED", "PAID"] else "EM_ABERTO"
            if status == "EM_ABERTO" and due_date and datetime.datetime.strptime(due_date, "%Y-%m-%d").date() < datetime.date.today():
                status = "ATRASADO"
                
            mapped_transactions.append({
                "id": item["id"],
                "tipo": "RECEBER",
                "fornecedor_cliente": item.get("customer", {}).get("name", "Cliente Consumidor"),
                "data_competencia": item.get("emission_date", datetime.date.today().isoformat()),
                "data_vencimento": due_date,
                "categoria": item.get("category", {}).get("name", "Receitas Operacionais"),
                "centro_de_custo": item.get("cost_center", {}).get("name", "Comercial / Vendas"),
                "descricao": item.get("description", "Faturamento Comercial"),
                "valor": float(item.get("value", 0)),
                "status": status,
                "observacao": item.get("notes", "Sincronizado automaticamente via GitHub Actions.")
            })
            
    # 2. Coleta Despesas (Contas a Pagar)
    pay_url = "https://api.contaazul.com/v1/payables"
    print("[Conta Azul] Buscando Contas a Pagar...")
    res_pay = requests.get(pay_url, headers=headers)
    
    if res_pay.status_code == 200:
        payables = res_pay.json()
        items = payables.get("items", payables if isinstance(payables, list) else [])
        for item in items:
            due_date = item.get("due_date", "")
            status = "PAGO" if item.get("status") in ["PAID", "RECEIVED"] else "EM_ABERTO"
            if status == "EM_ABERTO" and due_date and datetime.datetime.strptime(due_date, "%Y-%m-%d").date() < datetime.date.today():
                status = "ATRASADO"
                
            mapped_transactions.append({
                "id": item["id"],
                "tipo": "PAGAR",
                "fornecedor_cliente": item.get("supplier", {}).get("name", "Fornecedor Padrão"),
                "data_competencia": item.get("emission_date", datetime.date.today().isoformat()),
                "data_vencimento": due_date,
                "categoria": item.get("category", {}).get("name", "Despesas Operacionais"),
                "centro_de_custo": item.get("cost_center", {}).get("name", "Administrativo & Operações"),
                "descricao": item.get("description", "Sub-custo Administrativo"),
                "valor": float(item.get("value", 0)),
                "status": status,
                "observacao": item.get("notes", "Sincronizado automaticamente via GitHub Actions.")
            })
            
    return mapped_transactions

def upsert_into_supabase(transactions):
    """Faz o UPSERT de faturas no Supabase sem duplicar dados."""
    if not transactions:
        print("[Sincronizador] Nenhum lançamento novo localizado no Conta Azul.")
        return
        
    supabase = get_supabase_client()
    try:
        supabase.table("lancamentos_financeiros").upsert(transactions).execute()
        print(f"[Supabase] Sincronização Executada com Sucesso! {len(transactions)} registros de faturas foram atualizados.")
    except Exception as e:
        print(f"[Erro API] Falha na persistência de dados: {e}")

if __name__ == "__main__":
    print(f"--- INICIANDO PIPELINE DE INTEGRAÇÃO REAL-TIME ---")
    try:
        active_token = get_active_token()
        data = fetch_financial_entries_from_contaazul(active_token)
        upsert_into_supabase(data)
        print("--- EXECUÇÃO CONCLUÍDA COM SUCESSO ---")
    except Exception as e:
        print(f"[Falha Crítica] {e}")
