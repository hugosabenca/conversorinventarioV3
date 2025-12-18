import streamlit as st
import pandas as pd
import os
import glob
from openpyxl.utils import get_column_letter
from datetime import datetime
import json
import tempfile
import io

# =====================================================================================
# FUNÇÃO UNIFICADA: PROCESSA O ARQUIVO INDEPENDENTE DO TIPO
# =====================================================================================
def processar_arquivo_generico(caminho_csv):
    """
    Lê um arquivo CSV e tenta identificar automaticamente se é PRODUTO ACABADO
    ou BOBINA baseando-se na estrutura dos dados da coluna 4.
    """
    try:
        try:
            df = pd.read_csv(caminho_csv, header=None, encoding='utf-8', dtype=str)
        except UnicodeDecodeError:
            df = pd.read_csv(caminho_csv, header=None, encoding='latin1', dtype=str)
    except Exception as e:
        return None, f"Erro ao ler arquivo: {e}"

    dados_processados = []
    
    if df.empty:
        return pd.DataFrame(), None

    for index, row in df.iterrows():
        if len(row) < 5:
            continue

        dt_leitura_original = str(row[0]).strip()
        hr_leitura = str(row[1]).strip()
        coluna_tipo = str(row[3]).strip()
        dados_lidos = str(row[4]).strip()

        # Filtro de sujeira
        if "date" in dt_leitura_original.lower() or not dt_leitura_original[0].isdigit():
            continue

        # --- AJUSTE 1: Formatação da Data (Universal) ---
        # Aplica a formatação AGORA, antes de decidir se é PA ou Bobina
        try:
            # Tenta converter de mm-dd-yyyy para dd/mm/yyyy
            obj_date = datetime.strptime(dt_leitura_original, '%m-%d-%Y')
            dt_formatada = obj_date.strftime('%d/%m/%Y')
        except:
            # Se falhar (já estiver formatada ou erro), mantém a original
            dt_formatada = dt_leitura_original

        # Estrutura padrão da linha
        nova_linha = {
            "Data da Leitura": dt_formatada, # Usa a data já corrigida
            "Hora da Leitura": hr_leitura,
            "Filial": None,
            "Código": None,
            "Armazém": None,
            "Lote": None,
            "Peso": None, # None = Célula vazia no Excel
            "Localização": os.path.splitext(os.path.basename(caminho_csv))[0]
        }

        # ====================================================================
        # TESTE 1: É PRODUTO ACABADO CLÁSSICO? (Padrão: "XXX-XXX - YYY")
        # ====================================================================
        processado_como_pa = False
        if " -" in dados_lidos:
            try:
                partes_maiores = dados_lidos.split(" -", 1)
                
                parte_esq = partes_maiores[0].split("-")
                filial = parte_esq[0].strip() if len(parte_esq) > 0 else ""
                codigo = parte_esq[1].strip() if len(parte_esq) > 1 else ""
                
                parte_dir = partes_maiores[1].split("-") if len(partes_maiores) > 1 else []
                
                armazem = parte_dir[0].strip() if len(parte_dir) > 0 else ""
                lote = parte_dir[1].strip() if len(parte_dir) > 1 else ""
                peso_str = parte_dir[2].strip() if len(parte_dir) > 2 else "0"
                
                try:
                    peso_val = float(peso_str) / 1000.0
                except:
                    peso_val = None # Se der erro no PA, também deixa vazio

                nova_linha["Filial"] = filial
                nova_linha["Código"] = codigo
                nova_linha["Armazém"] = armazem
                nova_linha["Lote"] = lote
                nova_linha["Peso"] = peso_val
                
                dados_processados.append(nova_linha)
                processado_como_pa = True
            except Exception:
                pass
        
        if processado_como_pa:
            continue

        # ====================================================================
        # TESTE 2: É BOBINA? (Ou Bobina com estrutura completa)
        # ====================================================================
        
        # --- AJUSTE 2: Inicializa peso como None (Vazio) em vez de 0.0 ---
        lote_b = "erro"
        peso_b = None 

        # CASO 1: CODE128 (Asteriscos)
        if coluna_tipo == 'Code128' or '*' in dados_lidos:
            if ' ' in dados_lidos:
                 lote_b, peso_b = "erro de leitura", None
            elif '*' in dados_lidos:
                try:
                    partes = dados_lidos.split('*')
                    if dados_lidos.startswith('*'): 
                        lote_b = partes[3].strip()
                        peso_b = float(partes[2].strip()) / 1000.0
                    else: 
                        lote_b = partes[2].strip()
                        peso_b = float(partes[1].strip()) / 1000.0
                except:
                    lote_b, peso_b = "erro Code128/*", None
            elif dados_lidos.isdigit() and len(dados_lidos) <= 5:
                 # Se for muito curto e só numero, assume que é peso
                 try:
                    peso_b, lote_b = float(dados_lidos)/1000.0, ""
                 except:
                    peso_b, lote_b = None, dados_lidos
            else:
                 # Aqui cai o caso "8014270401" -> Peso fica None
                 lote_b, peso_b = dados_lidos, None

        # CASO 2: QR CODE / DATAMATRIX
        elif coluna_tipo in ['QR_CODE', 'QR', 'CODE_39', 'CODE_128'] or '{' in dados_lidos or ',' in dados_lidos:
            
            # 2.1 JSON
            if '{' in dados_lidos and '}' in dados_lidos:
                try:
                    partes = dados_lidos.split('{', 1)
                    identificador = partes[0].strip('"-')
                    dados_json = json.loads('{' + partes[1])
                    peso_b = float(dados_json.get('peso', 0))
                    lote_b = identificador
                except:
                    lote_b = "erro QR/JSON"

            # 2.2 NOVO FORMATO COM VÍRGULA
            elif ',' in dados_lidos and '-' in dados_lidos:
                try:
                    partes_virgula = dados_lidos.split(',')
                    
                    if len(partes_virgula) > 1 and partes_virgula[-1].replace('.', '', 1).isdigit():
                        peso_decimal = partes_virgula[-1].strip()
                        
                        parte_lote_completa = ','.join(partes_virgula[:-1])
                        partes_hifen = parte_lote_completa.split('-')
                        
                        # Extração de Filial/Código/Armazém se disponível
                        if len(partes_hifen) >= 4:
                            nova_linha["Filial"] = partes_hifen[0].strip()
                            nova_linha["Código"] = partes_hifen[1].strip()
                            nova_linha["Armazém"] = partes_hifen[2].strip()

                        lote_b = partes_hifen[-2].strip()
                        
                        peso_completo_str = f"{partes_hifen[-1].strip()},{peso_decimal}"
                        peso_b = float(peso_completo_str.replace(',', '.'))
                        
                    else:
                        raise ValueError("Formato virgula invalido")
                except:
                    try:
                        partes = dados_lidos.split('-')
                        lote_b = partes[3].strip()
                        peso_b = float(partes[-1].strip()) / 1000.0
                    except:
                        lote_b = "erro QR/FormatoVirgula"
            
            # 2.3 Formato Antigo (Só hifens)
            else:
                 try:
                     partes = dados_lidos.split('-')
                     if len(partes) >= 4:
                        lote_b = partes[3].strip() 
                        peso_b = float(partes[-1].strip()) / 1000.0
                     else:
                        lote_b = dados_lidos
                        peso_b = None
                 except:
                     lote_b = dados_lidos
                     peso_b = None

        else:
            lote_b = dados_lidos
            peso_b = None # Garante vazio se não reconhecer nada
        
        nova_linha["Lote"] = lote_b
        nova_linha["Peso"] = peso_b
        dados_processados.append(nova_linha)

    return pd.DataFrame(dados_processados), None

# =====================================================================================
# INTERFACE DO STREAMLIT (UI)
# =====================================================================================

st.set_page_config(page_title="Conversor de Inventário Dox", layout="wide")
st.title("Conversor de Inventário Unificado")
st.markdown("---")

col1, col2 = st.columns([2, 1])
with col1:
    uploaded_files = st.file_uploader(
        "Importar arquivos .csv (Aceita Bobina e Produto Acabado misturados)",
        type="csv",
        accept_multiple_files=True
    )

with col2:
    st.info("Configurações de Saída")
    nome_arquivo_usuario = st.text_input("Nome do Arquivo Final (sem .xlsx):", value="")

if st.button("Converter Arquivos", type="primary"):
    if not uploaded_files:
        st.warning("⚠️ Por favor, carregue pelo menos um arquivo .csv.")
    else:
        with st.spinner("Processando..."):
            try:
                todos_dfs = []
                for uploaded_file in uploaded_files:
                    with tempfile.NamedTemporaryFile(delete=False, suffix='.csv') as tmp_file:
                        tmp_file.write(uploaded_file.getbuffer())
                        tmp_path = tmp_file.name
                    
                    df_temp, erro = processar_arquivo_generico(tmp_path)
                    
                    if erro:
                        st.error(f"Erro no arquivo {uploaded_file.name}: {erro}")
                    elif not df_temp.empty:
                        df_temp["Localização"] = uploaded_file.name.replace('.csv', '')
                        todos_dfs.append(df_temp)
                    
                    os.unlink(tmp_path)

                if todos_dfs:
                    df_final = pd.concat(todos_dfs, ignore_index=True)
                    
                    # Tratamento estético
                    if "Armazém" in df_final.columns:
                        df_final["Armazém"] = df_final["Armazém"].fillna('').apply(lambda x: str(x).split('.')[0].zfill(2) if str(x).replace('.','').isdigit() else str(x))
                    
                    output = io.BytesIO()
                    with pd.ExcelWriter(output, engine='openpyxl') as writer:
                        df_final.to_excel(writer, index=False, sheet_name='Inventario Geral')
                        ws = writer.sheets['Inventario Geral']
                        for col in ws.columns:
                            max_len = max((len(str(cell.value)) for cell in col if cell.value is not None), default=0)
                            ws.column_dimensions[get_column_letter(col[0].column)].width = max_len + 4
                            
                            # Formatação de número apenas se tiver valor
                            if col[0].value == "Peso":
                                for cell in col[1:]:
                                    if cell.value is not None:
                                        cell.number_format = '0.000'

                    output.seek(0)
                    nome_download = f"{nome_arquivo_usuario.strip()}.xlsx" if nome_arquivo_usuario.strip() else "Inventario.xlsx"

                    st.success(f"✅ Sucesso! {len(todos_dfs)} arquivos processados.")
                    st.download_button(
                        label="📥 Baixar Excel Consolidado",
                        data=output,
                        file_name=nome_download,
                        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                    )
                else:
                    st.warning("Os arquivos foram lidos, mas nenhum dado válido foi encontrado.")

            except Exception as e:
                st.error(f"Ocorreu um erro crítico: {e}")