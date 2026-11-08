# Relatório de Validação, Cenários Testados e Limitações Conhecidas

Este documento registra a validação completa realizada sobre o **ConfereFiscal** utilizando arquivos fictícios de exemplo, bem como os formatos de relatórios PDF suportados automaticamente, aqueles que requerem mapeamento manual de colunas e as limitações técnicas conhecidas.

---

## 1. Cenários Validados com Arquivos Fictícios de Exemplo

Os arquivos fictícios de validação são gerados automaticamente na pasta `./exemplos` pelo comando `npm run generate-samples` e testados de ponta a ponta por `npm test` (`scripts/validate-e2e.js`):

### 1.1. Arquivos Gerados e Testados
- **`exemplos/pacote_vendas_exemplo.zip`** e **`exemplos/pacote_vendas_exemplo.rar`**:
  - Pacotes compactados contendo **14 arquivos XML** distribuídos em **subpastas** (`2026-10/PDV01_NFCe/`, `2026-10/Matriz_NFe/`, `2026-10/PDV01_Noturno/`, `2026-10/PDV02_SerieDivergente/`, `2026-10/Eventos_Cancelamento/`, `2026-10/Backup_Duplicados/`, `2026-10/Entradas_Devolucoes/`, `2026-10/Corrompidos/`) mais arquivo auxiliar `.txt` não-XML ignorado com segurança.
- **`exemplos/relatorio_vendas_exemplo.pdf`**:
  - Relatório gerencial de vendas em PDF com camada de texto selecionável contendo 9 registros de vendas.
- **`exemplos/relatorio_digitalizado_ocr.pdf`**:
  - Relatório em PDF sem camada de texto selecionável direta (simulando documento escaneado como imagem), utilizado para validar a detecção automática de PDF imagem e o fluxo de **OCR Local**.
- **`exemplos/demo_fb25.fdb`** e **`exemplos/demo_fb50.fdb`**:
  - Arquivos de banco com cabeçalho binário autêntico **ODS 11.2 (Firebird 2.5)** e **ODS 13.1 (Firebird 5.0)** para validação da inspeção de cabeçalho, detecção de incompatibilidade de ODS, descoberta de esquema (`TB_DOC_FISCAL_SAIDA` e `VW_RESUMO_CAIXA_PDV`), prévia SQL e conciliação em 3 vias.

### 1.2. Casos de Conciliação Testados e Comprovados
| Documento | Cenário Testado | Resultado Validado |
| :--- | :--- | :--- |
| **NFC-e 1001 (Série 1)** | Correspondência exata de Chave, Data, Valor (`R$ 150,00`), PIX e Status nas 3 fontes | **Conciliados** (`CONCILIADO`) |
| **NFC-e 1002 (Série 1)** | Venda com desconto (`R$ 300,00 - R$ 20,00 = R$ 280,00`) e destinatário ausente no XML | **Conciliados** (`CONCILIADO`) + registro explícito de campo ausente no XML |
| **NF-e 1003 (Série 1)** | XML com valor líquido `R$ 1.185,00` vs PDF com valor bruto `R$ 1.200,00` | **Divergências de valor** (`DIVERGENCIA_VALOR`) com diagnóstico automático de desconto/frete |
| **NFC-e 1004 (Série 1)** | Emitida em `15/10/2026 23:58:10` e autorizada em `16/10/2026 00:02:15` (PDF em `16/10/2026`) | **Divergências de data** quando analisado por *Data de Emissão*, e **Conciliados** ao alternar para *Data de Autorização* |
| **NFC-e 1005 (Série 1)** | Presente no XML (`R$ 450,00`) e no Firebird, porém ausente no relatório PDF | **Encontrados somente nos XMLs** (`SOMENTE_XML`) |
| **NFC-e 1006** | Número `1006` na Série `1` no XML, mas lançada na Série `2` no PDF e Firebird | **Divergências de número, série ou chave** (`DIVERGENCIA_IDENTIFICACAO`) |
| **NFC-e 1007 + Evento `110111`** | XML da nota + XML de Evento de Cancelamento `110111` na subpasta de eventos, mas ativa no PDF | **Cancelados, denegados ou inválidos** (`CANCELADO_DENEGADO_INVALIDO`) com alerta crítico |
| **NFC-e 1008 (Original + Cópia)** | Dois XMLs idênticos em subpastas diferentes | Original em **Conciliados** e cópia em **Possíveis duplicidades** (`POSSIVEL_DUPLICIDADE`) |
| **Doc 1009 e 1011** | Listados no relatório PDF sem arquivo XML correspondente | **Encontrados somente no PDF** (`SOMENTE_PDF`) |
| **NFC-e 1010 (Série 1)** | Mesmo valor (`R$ 310,00`) e data do Doc `1011` do PDF, porém numeração distinta | **Registros que precisam de revisão manual** (`REVISAO_MANUAL`) — sem correspondência silenciosa por valor |
| **NF-e 2050 (`cStat=302`)** | Uso Denegado pela SEFAZ | **Cancelados, denegados ou inválidos** (`CANCELADO_DENEGADO_INVALIDO`) |
| **NF-e 3090 (`tpNF=0`)** | Nota Fiscal de Entrada/Compra (não é operação de venda) | Isolada nos alertas e em **Cancelados, denegados ou inválidos** sem interromper os demais |
| **`XML_Malformado_Invalido.xml`** | Arquivo XML truncado sem tag de fechamento | Erro reportado individualmente por arquivo sem interromper o lote |

---

## 2. Formatos de PDF Suportados vs. Layouts que Exigem Configuração Manual

### 2.1. Formatos Reconhecidos Automaticamente
- Relatórios PDF com colunas delimitadas por pipe (`|`), ponto-e-vírgula (`;`), tabulação ou múltiplos espaços em branco (`2 ou mais espaços`).
- Relatórios PDF lineares padrão de ERPs/PDVs que contenham em cada linha ao menos uma data (`DD/MM/AAAA` ou `AAAA-MM-DD`), um número de documento e um valor monetário no padrão brasileiro (`1.234,56`).

### 2.2. Formatos e Layouts de PDF que Ainda Exigem Configuração ou Revisão Manual (Etapa 3)
1. **Relatórios com múltiplas colunas de valores monetários na mesma linha** (ex: *Valor Bruto*, *Desconto*, *Acréscimo*, *ICMS* e *Valor Líquido* lado a lado sem cabeçalho padronizado):
   - O usuário deve confirmar na **Etapa 3 (Mapeamento das Colunas do Relatório PDF)** qual coluna numérica corresponde ao **Valor Total da Venda** (`vNF`) para evitar comparar o valor líquido do XML com o valor bruto ou valor de imposto do PDF.
2. **Relatórios agrupados em múltiplos níveis hierárquicos** (ex: quando a *Data de Emissão* ou a *Série* aparece apenas uma vez no topo de um bloco/grupo e as linhas seguintes trazem apenas Número e Valor):
   - Linhas que não repetem a data precisam ter a data complementada ou revisada na tabela de **Prévia Editável do PDF** na Etapa 3.
3. **Relatórios PDF com quebras de linha dentro de um mesmo registro** (registros que ocupam 2 ou 3 linhas verticais por venda):
   - Exigem ajuste manual na tabela editável da Etapa 3 antes da execução da conciliação.
4. **PDFs Digitalizados com Baixa Resolução, Manuscritos ou Carimbos Sobrepostos**:
   - Embora o sistema detecte PDFs sem texto selecionável e ofereça **OCR Local**, o reconhecimento óptico pode trocar dígitos semelhantes (`0` vs `O`, `1` vs `7`, `5` vs `S`, `,` vs `.`) em imagens de baixa qualidade (< 200 DPI). Nesses casos, a conferência na **Prévia Editável do PDF (Etapa 3)** é indispensável.

---

## 3. Limitações Técnicas Conhecidas

1. **Arquivos `.zip` ou `.rar` protegidos por senha**:
   - Pacotes compactados criptografados com senha não podem ser abertos automaticamente; o usuário deve descompactá-los ou remover a senha antes da importação (o sistema exibe mensagem de erro clara para o arquivo sem travar a aplicação).
2. **Firebird 5.0 com `WireCrypt = Required` estrito ou plugin `Srp256` exclusivo**:
   - Conexões TCP locais com instâncias do Firebird 5.0 que estejam configuradas com `WireCrypt = Required` e sem `Legacy_Auth` habilitado no `firebird.conf` podem recusar o handshake de clientes wire Node.js padrão. O sistema diagnostica especificamente esse erro e orienta a habilitar `AuthServer = Srp256, Srp, Legacy_Auth` e `WireCrypt = Enabled` no `firebird.conf` para acesso local (`127.0.0.1`).
3. **Volume de Arquivos por Sessão**:
   - Por segurança e prevenção contra ataques de exaustão de memória (*ZipBomb*), cada upload é limitado a **150 MB por arquivo compactado**, **300 MB descomprimidos** e até **10.000 arquivos XML** por pacote.
