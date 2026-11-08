# ConfereFiscal — Conciliação e Auditoria Local de Vendas Fiscais

Aplicação web 100% local, em português do Brasil, para **conciliação e auditoria de vendas fiscais** confrontando:
1. **Arquivos XML de documentos fiscais** (importados via pacotes `.zip`, `.rar` com subpastas ou `.xml` avulsos — NF-e modelo 55, NFC-e modelo 65, CF-e SAT modelo 59, Eventos de Cancelamento `110111` e Inutilizações).
2. **Relatórios gerenciais/fiscais de vendas em PDF** (com extração tabular, heurística de linhas, mapeamento configurável de colunas, edição manual de prévia e detecção de PDF digitalizado com OCR local).
3. **Banco de dados Firebird 2.5 (ODS 11.2) ou 5.0 (ODS 13.1)** (com detecção automática de versão, descoberta dinâmica do esquema `RDB$RELATIONS` / `RDB$RELATION_FIELDS`, prévia SQL e execução estritamente `READ ONLY`).

---

## Instalador Autônomo para Windows (Esta Máquina e Outras Máquinas)

Na pasta `instalador/` estão disponíveis os pacotes prontos para instalar ou executar o **ConfereFiscal** em **qualquer computador com Windows 10 ou 11**, **sem precisar instalar Python ou Node.js** nas outras máquinas (o runtime Python 3.12 + SQLite3 + Tkinter já vai 100% embutido):

1. **Instalador Gráfico Único (`instalador/Instalador_ConfereFiscal_Setup.exe` — ~13,6 MB)**:
   - Arquivo `.exe` único e autossuficiente (basta copiar este único arquivo para um pendrive ou pasta de rede e executar em qualquer máquina).
   - Abre o assistente gráfico de instalação em português, permitindo escolher a pasta de destino (padrão: `%LOCALAPPDATA%\Programs\ConfereFiscal`, sem exigir senha de Administrador), criar atalhos na **Área de Trabalho** e no **Menu Iniciar**, preservar a base local `dados_locais/confere_fiscal_local.db` em atualizações e registrar o desinstalador no Windows.
   - Instala os executáveis nativos:
     - `ConfereFiscal.exe` (abre a Interface Desktop Nativa).
     - `ConfereFiscal_Web.exe` (inicia o Servidor Web Local na porta `5000` e abre o navegador).
     - `Desinstalar_ConfereFiscal.exe` (remove o aplicativo com opção de manter ou excluir o banco local).
   - **Instalação Silenciosa (Linha de Comando / TI)**:
     ```powershell
     .\instalador\Instalador_ConfereFiscal_Setup.exe /SILENT /DIR="C:\ConfereFiscal"
     ```
2. **Pacote Portátil (`instalador/ConfereFiscal_Portatil_v1.0.0.zip` — ~13,6 MB)**:
   - Basta descompactar em qualquer pasta ou pendrive e dar duplo-clique em `ConfereFiscal.exe` ou `ConfereFiscal_Web.exe`.
3. **Como regerar o instalador após alterações no código**:
   ```powershell
   .\.venv\Scripts\python.exe scripts/build_installer.py
   ```

---

## Requisitos e Execução Local Rápida (Ambiente de Desenvolvimento)

O projeto conta com **duas opções completas de execução 100% local**:
1. **Aplicação Local em Python (`python_app/`)** — com **Interface Desktop Nativa (Tkinter/TTK)** para trabalhar diretamente com a base de dados local (`dados_locais/confere_fiscal_local.db`), Console SQL somente-leitura, inspetor de arquivos Firebird `.fdb` (2.5 e 5.0), importador de XMLs para o banco e Servidor Web Python na porta `5000`.
2. **Servidor Web Node.js (`src/server.js`)** — com interface web SPA completa na porta `3000`.

### Opção A — Aplicação Local em Python (Desktop GUI + Gerenciador de Base Local + Web)

No diretório raiz do projeto (`c:\Work\Pessoal\Projetos\ConfereFiscal`), utilize o ambiente virtual `.venv` configurado com Python 3.12:

```powershell
# 1. Iniciar a Interface Desktop Nativa em Python (Gerenciador da Base Local, SQL, Importação e Conciliação)
.\.venv\Scripts\python.exe -m python_app.desktop_app
# (ou dê duplo-clique em iniciar_python_desktop.bat)

# 2. Iniciar o Servidor Web em Python na porta 5000 (http://127.0.0.1:5000)
.\.venv\Scripts\python.exe -m python_app.web_server
# (ou dê duplo-clique em iniciar_python_web.bat)

# 3. Executar a suíte de testes automatizados da aplicação Python
.\.venv\Scripts\python.exe -m unittest python_app/test_python_app.py -v
```

#### Recursos para trabalhar com a Base Local na Aplicação Python:
- **Banco de Dados Local Ativo**: `dados_locais/confere_fiscal_local.db` (esquema compatível com Firebird: tabelas `TB_DOC_FISCAL_SAIDA`, `TB_DOC_FISCAL_ITENS`, `TB_DOC_FISCAL_PAGAMENTOS` e view `VW_RESUMO_CAIXA_PDV`).
- **Aba 1 — Trabalhar na Base Local**: Pesquise, filtre por situação (`A`, `C`, `D`, `I`), adicione novos documentos fiscais, edite valores/datas/pagamentos/chaves de registros existentes, exclua registros ou restaure as 10 vendas padrão.
- **Aba 2 — Console SQL & Inspeção Firebird (2.5 / 5.0)**: Execute consultas `SELECT` (incluindo sintaxe Firebird `SELECT FIRST N ...`) sobre as tabelas e views locais e inspecione o cabeçalho binário ODS de qualquer arquivo `.fdb` / `.gdb` (`ODS 11.2` = Firebird 2.5; `ODS 13.1` = Firebird 5.0).
- **Aba 3 — Importar XMLs (`.zip`/`.rar`/`.xml`) e PDF para a Base Local**: Extraia pacotes compactados com subpastas e sincronize/grave diretamente os documentos fiscais de venda na tabela `TB_DOC_FISCAL_SAIDA` (mesclando ou substituindo os registros existentes).
- **Aba 4 — Conciliação Fiscal & Exportação**: Confronte `XML × PDF × Base Local` alternando entre Data de Emissão e Data de Autorização, e exporte para `.csv`, `.xlsx` e `.pdf`.

---

### Opção B — Servidor Web Local em Node.js

#### Pré-requisitos
- **Node.js** versão 18 ou superior (validado em Node.js v24 no Windows).
- Não exige conexão com a internet para funcionar: a aplicação conta com motores nativos embutidos em `src/lib/` (além de suporte automático a pacotes opcionais caso instalados via `npm install`).

#### Como iniciar o servidor Node.js localmente

No diretório raiz do projeto (`c:\Work\Pessoal\Projetos\ConfereFiscal`), execute:

```powershell
# 1. (Opcional) Gerar os arquivos fictícios de exemplo na pasta ./exemplos
npm run generate-samples

# 2. (Opcional) Executar a bateria automatizada de validação de ponta a ponta
npm test

# 3. Iniciar o servidor web local
npm start
```

Em seguida, abra no navegador:
- **http://127.0.0.1:3000**

---

## Fluxo Guiado em 7 Etapas

1. **Etapa 1 — Importar XMLs (`.zip`, `.rar`, `.xml`)**:
   - Extrai recursivamente todos os XMLs contidos na raiz e em subpastas dos arquivos `.zip` e `.rar`.
   - Apresenta andamento e ocorrências por arquivo (XML malformado, NF-e de entrada `tpNF=0`, devoluções `finNFe=4`, remessas) sem interromper a leitura dos demais documentos.
   - Correlaciona automaticamente arquivos de evento de cancelamento (`procEventoNFe` / `tpEvento=110111`) à chave de acesso da nota original.
   - Detecta duplicidades (mesmo hash SHA-256, mesma chave de 44 dígitos ou mesmo Número + Série + Modelo) e sinaliza claramente campos ausentes no XML.
2. **Etapa 2 — Importar Relatório PDF**:
   - Extrai cabeçalhos e linhas do relatório de vendas.
   - Detecta automaticamente quando um PDF foi digitalizado como imagem (sem camada de texto selecionável) e oferece **OCR Local** imediato.
3. **Etapa 3 — Revisar Dados e Mapear Campos**:
   - Permite configurar/confirmar o mapeamento das colunas do PDF (Data, Número, Série, Modelo, Valor Total, Forma de Pagamento, Status e Chave de Acesso).
   - Exibe tabela de prévia editável para corrigir manualmente células, excluir subtotais/rodapés ou adicionar linhas antes da conciliação.
4. **Etapa 4 — Configurar e Testar Firebird (Opcional — 2.5 e 5.0)**:
   - Permite configurar Host, Porta, Caminho do arquivo `.fdb`/`.gdb` ou Alias, Usuário, Senha, Charset e Versão alvo (`Auto-detectar`, `Firebird 2.5` ou `Firebird 5.0`).
   - Inspeciona o cabeçalho binário ODS do arquivo local (`ODS 11.2` = Firebird 2.5; `ODS 13.1` = Firebird 5.0) e consulta a versão da engine via SQL.
   - Descobre tabelas, views e colunas do esquema sem presumir nomes fixos e gera prévia SQL somente-leitura (`SELECT FIRST 15 ...`).
5. **Etapa 5 — Executar Conciliação**:
   - Permite escolher entre **Data de Emissão (`dhEmi`)** e **Data de Autorização (`dhRecbto`)**, confronto de forma de pagamento e confronto em 3 vias com o Firebird.
6. **Etapa 6 — Revisar Divergências**:
   - Classifica os registros nas **9 categorias obrigatórias**:
     1. *Conciliados*
     2. *Encontrados somente nos XMLs*
     3. *Encontrados somente no PDF*
     4. *Divergências de data*
     5. *Divergências de valor*
     6. *Divergências de número, série ou chave*
     7. *Cancelados, denegados ou inválidos*
     8. *Possíveis duplicidades*
     9. *Registros que precisam de revisão manual*
   - Não realiza correspondências silenciosas por valor aproximado; exibe o critério exato de vínculo e permite confirmar, rejeitar ou reclassificar casos ambíguos com nota de auditoria.
7. **Etapa 7 — Exportar Resultados**:
   - Exporta os resultados (respeitando os filtros ativos) para **CSV** (UTF-8 com BOM), **Excel (`.xlsx` multi-abas)** e **Relatório PDF executivo**, incluindo data/hora da análise, arquivos utilizados, filtros e critérios de comparação.

---

## Segurança e Operação Local

- **Zero Envio Externo**: Nenhum arquivo XML, PDF, credencial ou dado do Firebird sai da máquina local (`127.0.0.1`).
- **Preservação dos Originais**: Os arquivos importados nunca são alterados nem excluídos.
- **Armazenamento Temporário Transparente**: Todos os arquivos temporários, logs sanitizados e configurações ficam centralizados em `.local_data/` (visível no botão **"Armazenamento & Segurança Local"** no topo da interface, com opção de limpeza imediata mediante confirmação).
- **Proteção de Credenciais**: Quando salva localmente, a senha do Firebird é criptografada com **AES-256-GCM** (`.local_data/config/firebird.enc.json`) e é automaticamente mascarada (`[PROTEGIDO]`) em qualquer log técnico.
- **Consultas Estritamente Somente-Leitura**: Transações abertas com `ISOLATION_READ_COMMITTED_READ_ONLY` e validador sintático que bloqueia qualquer comando diferente de `SELECT`.

Consulte também [docs/VALIDACAO_E_LIMITACOES.md](docs/VALIDACAO_E_LIMITACOES.md) para detalhes sobre os testes realizados e limitações conhecidas.
