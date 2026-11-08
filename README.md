# ConfereFiscal — Conferência de vendas fiscais

Aplicativo de computador (Windows), 100% local, para conferir as vendas de um período comparando três fontes:

1. **Banco de dados do Clipp** (Firebird 2.5 ou 5.0, arquivo `.FDB`) — leitura somente, sem alterar nada.
2. **XMLs das notas** (NF-e modelo 55, NFC-e modelo 65), avulsos ou dentro de `.zip` / `.rar`, inclusive com subpastas e eventos de cancelamento.
3. **Relatório de vendas em PDF** exportado pelo ERP (ex.: "Relatório de Vendas" do Clipp). O total lido é conferido com os totais impressos no rodapé do relatório.

O resultado mostra o que bate e o que diverge (nota sem XML, XML fora do banco, valor diferente, nota autorizada em outro dia, canceladas etc.) e pode ser salvo em Excel ou PDF.

Nenhum arquivo, credencial ou dado sai da máquina. A única conexão externa é a verificação de atualizações no GitHub.

---

## Instalar em outro computador

Na pasta `instalador/` (gerada pelo script de build, não vai para o Git):

- **`Instalador_ConfereFiscal_Setup.exe`** — instalador único. Instala em `%LOCALAPPDATA%\Programs\ConfereFiscal` (não pede senha de administrador), cria atalhos e preserva o banco local em reinstalações. Não precisa ter Python instalado: o runtime vai embutido.
  - Instalação silenciosa: `Instalador_ConfereFiscal_Setup.exe /SILENT /DIR="C:\ConfereFiscal"`
- **`ConfereFiscal_Portatil_vX.Y.Z.zip`** — versão portátil: descompacte e abra `ConfereFiscal.exe`.

## Atualização automática

O botão **Verificar atualização** (canto superior direito) consulta o `version.json` do repositório
[junin1737/conferefiscal](https://github.com/junin1737/conferefiscal). Se a versão publicada for maior que a instalada,
o app baixa o código novo, faz backup da versão atual (`.local_data/backups/`), instala e reinicia sozinho.
Os dados locais (`dados_locais/`, `.local_data/`) nunca são alterados.

Ao abrir, o app também confere em segundo plano e avisa no botão quando há versão nova.

### Publicar uma nova versão

```powershell
.\.venv\Scripts\python.exe scripts/release_version.py patch "Descrição da mudança" --push
```

Isso aumenta a versão em `version.json` e `python_app/__init__.py`, cria o commit/tag e envia ao GitHub.
Use `minor` ou `major` para saltos maiores e `--build` para gerar também o instalador.

---

## Desenvolvimento

Requisitos: Python 3.12 (o projeto usa somente a biblioteca padrão; ambiente em `.venv`).

```powershell
# Abrir o aplicativo
.\.venv\Scripts\python.exe -m python_app.desktop_app
# (ou duplo-clique em iniciar_python_desktop.bat)

# Rodar os testes
.\.venv\Scripts\python.exe -m unittest python_app/test_python_app.py -v

# Gerar instalador e pacote portátil
.\.venv\Scripts\python.exe scripts/build_installer.py
```

### Estrutura

| Arquivo | Função |
| :--- | :--- |
| `python_app/desktop_app.py` | Telas do aplicativo (Tkinter) |
| `python_app/database_manager.py` | Base local SQLite e leitura do banco Firebird/Clipp |
| `python_app/fiscal_parsers.py` | Leitura de XMLs, `.zip` e `.rar` |
| `python_app/pdf_text.py` | Extração de texto de PDF (fontes Identity-H/ToUnicode, sem dependências) |
| `python_app/pdf_report.py` | Interpretação da tabela de vendas do relatório PDF |
| `python_app/reconciliation_engine.py` | Cruzamento banco × XML × PDF e exportação Excel/PDF |
| `python_app/version_manager.py` | Verificação e instalação de atualizações pelo GitHub |
| `scripts/build_installer.py` | Gera o instalador `.exe` e o pacote portátil |
| `scripts/release_version.py` | Publica uma nova versão |

## Como usar

1. **Banco de dados do Clipp** — escolha o arquivo `.FDB` e clique em *Ler banco de dados*.
2. **Período** — informe as datas, ou deixe em branco: ao importar o PDF/XMLs o período é preenchido com as datas dos arquivos.
3. **Arquivos** — importe os XMLs (`.zip`, `.rar`, `.xml` ou uma pasta) e/ou o relatório PDF.
4. Veja o **Resumo** e a aba **Diferenças encontradas**; salve o resultado em Excel ou PDF.

Limitações conhecidas: veja [docs/VALIDACAO_E_LIMITACOES.md](docs/VALIDACAO_E_LIMITACOES.md).
