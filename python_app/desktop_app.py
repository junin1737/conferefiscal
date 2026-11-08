import os
import re
import traceback
import threading
import webbrowser
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple
from http.server import ThreadingHTTPServer

from python_app.database_manager import (
    LocalDatabaseManager,
    ROOT_DIR,
    DEFAULT_CLIPP_FDB_PATH,
    CFOP_DESCRIPTIONS,
    inspect_fdb_ods_header,
    normalize_iso_date,
)
from python_app.fiscal_parsers import (
    extract_xml_containers,
    extract_xml_from_folder,
    analyze_extracted_xmls,
    parse_sales_pdf_bytes,
    perform_local_ocr_pdf_bytes,
)
from python_app.reconciliation_engine import (
    run_reconciliation,
    generate_csv_export_bytes,
    generate_xlsx_export_bytes,
    generate_pdf_export_bytes,
)
from python_app.version_manager import (
    get_installed_version_info,
    check_published_version,
    apply_published_update,
)
from python_app.web_server import ConfereFiscalPythonHandler


# =============================================================================
# PALETA EXECUTIVA MODERNA (SLATE / INDIGO / EMERALD CORPORATE SKIN)
# =============================================================================
PALETTE = {
    "app_bg": "#f1f5f9",          # Fundo geral da área de trabalho (Slate 100)
    "card_bg": "#ffffff",         # Superfície dos cards e tabelas
    "card_border": "#cbd5e1",     # Borda suave 1px dos cards (Slate 300)
    "card_sub_bg": "#f8fafc",     # Faixas internas suaves (Slate 50)
    "text_primary": "#0f172a",    # Texto escuro principal (Slate 900)
    "text_secondary": "#475569",  # Texto secundário (Slate 600)
    "text_muted": "#64748b",      # Texto discreto (Slate 500)
    # Sidebar Executiva Escura
    "sb_bg": "#0b1120",           # Fundo da barra lateral (Midnight Slate)
    "sb_card": "#111c33",         # Card interno da sidebar
    "sb_card_border": "#1e293b",  # Borda sutil do card na sidebar
    "sb_input_bg": "#070d19",     # Fundo dos campos de digitação na sidebar
    "sb_input_fg": "#f8fafc",     # Texto dos campos na sidebar
    "sb_input_border": "#334155", # Borda padrão do input
    "sb_input_focus": "#38bdf8",  # Borda em foco (Sky 400)
    "sb_text": "#f1f5f9",
    "sb_muted": "#94a3b8",
    # Cores de Ação e Status
    "blue": "#2563eb",
    "blue_hover": "#1d4ed8",
    "blue_light": "#eff6ff",
    "sky": "#0284c7",
    "sky_hover": "#0369a1",
    "indigo": "#4f46e5",
    "indigo_hover": "#4338ca",
    "emerald": "#059669",
    "emerald_hover": "#047857",
    "emerald_bg": "#ecfdf5",
    "amber": "#d97706",
    "amber_bg": "#fffbeb",
    "rose": "#e11d48",
    "rose_bg": "#fff1f2",
    "disabled_bg": "#1e293b",
    "disabled_fg": "#64748b",
    # Tabelas
    "table_header_bg": "#1e293b",
    "table_header_fg": "#f8fafc",
    "row_even": "#ffffff",
    "row_odd": "#f8fafc",
    "row_select_bg": "#dbeafe",
    "row_select_fg": "#1e3a8a",
}


def _resolve_icon_path() -> Optional[Path]:
    candidates = [
        Path(r"C:\Work\Pessoal\Projetos\ConfereFiscal\instalador\icone.png"),
        ROOT_DIR / "instalador" / "icone.png",
        ROOT_DIR / "icone.png",
    ]
    for p in candidates:
        if p.exists():
            return p
    return None


def _fmt_brl(val: Any) -> str:
    """Formata número em moeda brasileira: R$ 1.234,56"""
    try:
        num = float(val or 0.0)
    except (ValueError, TypeError):
        num = 0.0
    sign = "-" if num < -0.004 else ""
    s = f"{abs(num):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"{sign}R$ {s}"


def _fmt_date_br(raw: Any) -> str:
    """Formata data ISO ('2026-10-15' ou '2026-2-07') para padrão brasileiro '15/10/2026'."""
    if not raw or raw == "—":
        return "—"
    iso = normalize_iso_date(str(raw))
    if iso and len(iso) == 10 and iso[4] == "-" and iso[7] == "-":
        return f"{iso[8:10]}/{iso[5:7]}/{iso[0:4]}"
    return str(raw)


def _fmt_datetime_br(raw: Any) -> str:
    """Formata data/hora SEFAZ ('2026-10-15T09:15:04') para '15/10/2026 09:15:04'."""
    if not raw or raw == "—":
        return "—"
    s = str(raw).strip()
    m = re.match(r"^(\d{4})[/\-](\d{1,2})[/\-](\d{1,2})(?:[T\s]+(\d{1,2}:\d{2}(?::\d{2})?))?", s)
    if m:
        d_br = f"{int(m.group(3)):02d}/{int(m.group(2)):02d}/{int(m.group(1)):04d}"
        t_part = m.group(4)
        return f"{d_br} {t_part}" if t_part and t_part != "00:00:00" else d_br
    return _fmt_date_br(s)


class ModernButton(tk.Frame):
    """
    Botão flat corporativo moderno sem bordas 3D antigas do Tkinter,
    com efeito hover suave e suporte explícito a estado habilitado/bloqueado.
    """

    def __init__(
        self,
        parent: tk.Widget,
        text: str,
        command: Callable[[], None],
        bg: str = PALETTE["blue"],
        hover_bg: str = PALETTE["blue_hover"],
        fg: str = "#ffffff",
        font: Tuple[Any, ...] = ("Segoe UI", 9, "bold"),
        pady: int = 6,
        padx: int = 10,
        enabled: bool = True,
        disabled_callback: Optional[Callable[[], None]] = None,
    ):
        super().__init__(parent, bg=bg if enabled else PALETTE["disabled_bg"], bd=0, highlightthickness=0)
        self._command = command
        self._bg = bg
        self._hover_bg = hover_bg
        self._fg = fg
        self._enabled = enabled
        self._disabled_callback = disabled_callback

        self.lbl = tk.Label(
            self,
            text=text,
            bg=bg if enabled else PALETTE["disabled_bg"],
            fg=fg if enabled else PALETTE["disabled_fg"],
            font=font,
            padx=padx,
            pady=pady,
            cursor="hand2" if enabled else "arrow",
        )
        self.lbl.pack(fill="both", expand=True)

        for w in (self, self.lbl):
            w.bind("<Enter>", self._on_enter)
            w.bind("<Leave>", self._on_leave)
            w.bind("<Button-1>", self._on_click)

    def set_enabled(self, enabled: bool) -> None:
        self._enabled = bool(enabled)
        bg = self._bg if self._enabled else PALETTE["disabled_bg"]
        fg = self._fg if self._enabled else PALETTE["disabled_fg"]
        cur = "hand2" if self._enabled else "arrow"
        self.configure(bg=bg)
        self.lbl.configure(bg=bg, fg=fg, cursor=cur)

    def set_text(self, text: str) -> None:
        self.lbl.configure(text=text)

    def _on_enter(self, _event: Any = None) -> None:
        if self._enabled:
            self.configure(bg=self._hover_bg)
            self.lbl.configure(bg=self._hover_bg)

    def _on_leave(self, _event: Any = None) -> None:
        bg = self._bg if self._enabled else PALETTE["disabled_bg"]
        self.configure(bg=bg)
        self.lbl.configure(bg=bg)

    def _on_click(self, _event: Any = None) -> None:
        if self._enabled:
            self._command()
        elif self._disabled_callback is not None:
            self._disabled_callback()


class ConfereFiscalDesktopApp:
    """
    Dashboard Desktop Executivo em Python para Conciliação e Auditoria Fiscal Contábil
    (NF-e mod. 55, NFC-e mod. 65, Eventos 101/102, Tabelas Dinâmicas CFOP/NCM,
    Desafio Temporal D+1 Emissão vs Autorização SEFAZ e Matriz de Divergências).

    Regras de Operação:
    1. Todos os valores iniciam zerados (R$ 0,00).
    2. É obrigatório informar e aplicar o Filtro de Período (Data Inicial e Data Final)
       no Passo 2 para exibir os valores do banco no período e habilitar a importação
       dos lotes XML (.zip, .rar, .xml) e PDF no Passo 3.
    """

    def __init__(self, root: tk.Tk):
        self.root = root
        self.version_info: Dict[str, Any] = get_installed_version_info()
        self.installed_version: str = str(self.version_info.get("version") or "1.1.0")
        self.last_version_check: Optional[Dict[str, Any]] = None
        self.root.title(f"ConfereFiscal v{self.installed_version} — Auditoria e Conciliação Fiscal Contábil (NF-e 55 / NFC-e 65)")
        self.root.geometry("1460x880")
        self.root.minsize(1220, 740)
        self.root.configure(bg=PALETTE["app_bg"])

        self.icon_photo: Optional[tk.PhotoImage] = None
        self.sidebar_logo_photo: Optional[tk.PhotoImage] = None
        self._load_window_icon()

        self.db = LocalDatabaseManager()
        self.xml_entries: List[Dict[str, Any]] = []
        self.xml_documents: List[Dict[str, Any]] = []
        self.xml_alerts: List[Dict[str, str]] = []
        self.xml_summary: Dict[str, Any] = {}
        self.loaded_archive_names: List[str] = []

        self.pdf_records: List[Dict[str, Any]] = []
        self.pdf_last_bytes: bytes = b""
        self.pdf_last_name: str = ""

        # Estado obrigatório de filtro de período: inicia sempre False (valores zerados e XML bloqueado)
        self.period_filter_applied: bool = False
        self.applied_dt_inicio_iso: Optional[str] = None
        self.applied_dt_fim_iso: Optional[str] = None

        self.reconciliation_result: Optional[Dict[str, Any]] = None
        self.audit_dashboard: Optional[Dict[str, Any]] = None
        self.last_db_audit_data: Dict[str, Any] = {}
        self.manual_decisions: Dict[str, Dict[str, Any]] = {}
        self.web_server_thread: Optional[threading.Thread] = None

        desktop_dir = Path.home() / "Desktop"
        self.last_browse_dir: str = str(desktop_dir if desktop_dir.exists() else Path.home())

        default_fdb = (
            DEFAULT_CLIPP_FDB_PATH
            if os.path.exists(DEFAULT_CLIPP_FDB_PATH)
            else str(ROOT_DIR / "exemplos" / "demo_fb50.fdb")
        )
        self.var_db_host = tk.StringVar(value="127.0.0.1")
        self.var_db_port = tk.StringVar(value="3050")
        self.var_db_path = tk.StringVar(value=default_fdb)
        self.var_db_user = tk.StringVar(value="SYSDBA")
        self.var_db_pass = tk.StringVar(value="masterkey")

        self.var_dt_inicio = tk.StringVar(value="")
        self.var_dt_fim = tk.StringVar(value="")
        self.var_date_base = tk.StringVar(value="issueDate")  # issueDate | authDate
        self.var_matrix_filter = tk.StringVar(value="ALL")
        self.var_matrix_search = tk.StringVar(value="")
        self.var_events_filter = tk.StringVar(value="ALL")
        self.var_search_55 = tk.StringVar(value="")
        self.var_search_65 = tk.StringVar(value="")

        self.active_tab_key: str = "overview"
        self.tab_frames: Dict[str, tk.Frame] = {}
        self.tab_buttons: Dict[str, Tuple[tk.Frame, tk.Label, tk.Frame]] = {}
        self.model_sub_State: Dict[str, Tuple[Dict[str, tk.Frame], Dict[str, tk.Label]]] = {}

        self._configure_ttk_styles()
        self._build_main_layout()

        # Inicializa na tela com todos os valores ZERADOS e importação bloqueada até aplicar o período
        self.refresh_all_views()

    def _load_window_icon(self) -> None:
        icon_path = _resolve_icon_path()
        if icon_path and icon_path.exists():
            try:
                self.icon_photo = tk.PhotoImage(file=str(icon_path))
                self.root.iconphoto(True, self.icon_photo)
                w = max(self.icon_photo.width() // 36, 1)
                self.sidebar_logo_photo = self.icon_photo.subsample(w, w)
            except Exception:
                pass

    def _configure_ttk_styles(self) -> None:
        style = ttk.Style(self.root)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass

        # Estilo limpo e moderno para Treeviews (Tabelas Executivas)
        style.configure(
            "Executive.Treeview",
            background=PALETTE["row_even"],
            fieldbackground=PALETTE["row_even"],
            foreground=PALETTE["text_primary"],
            rowheight=30,
            borderwidth=0,
            relief="flat",
            font=("Segoe UI", 9),
        )
        style.configure(
            "Executive.Treeview.Heading",
            background=PALETTE["table_header_bg"],
            foreground=PALETTE["table_header_fg"],
            relief="flat",
            borderwidth=0,
            padding=(10, 8),
            font=("Segoe UI", 9, "bold"),
        )
        style.map(
            "Executive.Treeview.Heading",
            background=[("active", "#334155")],
            foreground=[("active", "#ffffff")],
        )
        style.map(
            "Executive.Treeview",
            background=[("selected", PALETTE["row_select_bg"])],
            foreground=[("selected", PALETTE["row_select_fg"])],
        )

        # Scrollbar discreta
        style.configure(
            "Vertical.TScrollbar",
            gripcount=0,
            background="#cbd5e1",
            darkcolor="#cbd5e1",
            lightcolor="#cbd5e1",
            troughcolor="#f1f5f9",
            bordercolor="#f1f5f9",
            arrowcolor="#475569",
        )

    def _configure_tree_tags(self, tree: ttk.Treeview) -> None:
        tree.tag_configure("even", background=PALETTE["row_even"], foreground=PALETTE["text_primary"])
        tree.tag_configure("odd", background=PALETTE["row_odd"], foreground=PALETTE["text_primary"])
        tree.tag_configure("ok", background="#f0fdf4", foreground="#065f46")
        tree.tag_configure("err", background="#fff1f2", foreground="#9f1239")
        tree.tag_configure("warn", background="#fffbeb", foreground="#92400e")
        tree.tag_configure("zero", background="#ffffff", foreground="#94a3b8")
        tree.tag_configure("bold", background="#eff6ff", foreground="#1e3a8a", font=("Segoe UI", 9, "bold"))

    def _create_surface_card(
        self,
        parent: tk.Widget,
        accent_top: Optional[str] = None,
        padx: int = 12,
        pady: int = 10,
        bg: str = PALETTE["card_bg"],
    ) -> Tuple[tk.Frame, tk.Frame]:
        """
        Cria um Card moderno com borda fina suave (#cbd5e1) e faixa de acento opcional no topo.
        Retorna (outer_border_frame, inner_content_frame).
        """
        outer = tk.Frame(parent, bg=PALETTE["card_border"], bd=0, highlightthickness=0, padx=1, pady=1)
        surface = tk.Frame(outer, bg=bg, bd=0, highlightthickness=0)
        surface.pack(fill="both", expand=True)
        if accent_top:
            bar = tk.Frame(surface, bg=accent_top, height=4, bd=0, highlightthickness=0)
            bar.pack(fill="x", side="top")
        inner = tk.Frame(surface, bg=bg, bd=0, highlightthickness=0, padx=padx, pady=pady)
        inner.pack(fill="both", expand=True)
        return outer, inner

    def _create_sidebar_entry(
        self,
        parent: tk.Widget,
        textvariable: tk.StringVar,
        width: int = 14,
        show: str = "",
        justify: str = "left",
        font: Tuple[Any, ...] = ("Segoe UI", 9),
    ) -> tk.Entry:
        ent = tk.Entry(
            parent,
            textvariable=textvariable,
            width=width,
            show=show,
            justify=justify,
            bg=PALETTE["sb_input_bg"],
            fg=PALETTE["sb_input_fg"],
            insertbackground=PALETTE["sb_input_focus"],
            relief="flat",
            bd=4,
            highlightthickness=1,
            highlightbackground=PALETTE["sb_input_border"],
            highlightcolor=PALETTE["sb_input_focus"],
            font=font,
        )
        return ent

    def _bind_date_mask(self, entry: tk.Entry, var: tk.StringVar) -> None:
        """Formata automaticamente DD/MM/AAAA conforme o usuário digita números."""
        def _on_key_release(event: Any) -> None:
            if event.keysym in ("BackSpace", "Delete", "Left", "Right", "Tab", "Return", "Home", "End"):
                return
            raw = var.get()
            digits = re.sub(r"\D", "", raw)[:8]
            if not digits:
                return
            if len(digits) <= 2:
                formatted = digits
            elif len(digits) <= 4:
                formatted = f"{digits[:2]}/{digits[2:]}"
            else:
                formatted = f"{digits[:2]}/{digits[2:4]}/{digits[4:8]}"
            if formatted != raw:
                var.set(formatted)
                entry.icursor("end")

        entry.bind("<KeyRelease>", _on_key_release)

    # =========================================================================
    # ESTRUTURA PRINCIPAL: SIDEBAR EXECUTIVA + ÁREA DE TRABALHO
    # =========================================================================

    def _build_main_layout(self) -> None:
        self.main_container = tk.Frame(self.root, bg=PALETTE["app_bg"])
        self.main_container.pack(fill="both", expand=True)

        # 1. MENU LATERAL (SIDEBAR ESCURA EXECUTIVA)
        self.sidebar = tk.Frame(self.main_container, bg=PALETTE["sb_bg"], width=345, padx=12, pady=12)
        self.sidebar.pack(side="left", fill="y")
        self.sidebar.pack_propagate(False)

        self._build_sidebar_content()

        # 2. ÁREA PRINCIPAL DIREITA
        self.workspace_area = tk.Frame(self.main_container, bg=PALETTE["app_bg"], padx=14, pady=10)
        self.workspace_area.pack(side="left", fill="both", expand=True)

        self._build_top_header_banner()
        self._build_top_kpi_bar()
        self._build_navigation_tabs_and_pages()

    def _build_sidebar_card(self, step_tag: str, title: str, badge_text: str = "", badge_bg: str = "#1e293b", badge_fg: str = "#38bdf8") -> Tuple[tk.Frame, tk.Label]:
        outer = tk.Frame(self.sidebar, bg=PALETTE["sb_card_border"], padx=1, pady=1)
        outer.pack(fill="x", pady=(0, 9))
        inner = tk.Frame(outer, bg=PALETTE["sb_card"], padx=10, pady=8)
        inner.pack(fill="both", expand=True)

        hdr = tk.Frame(inner, bg=PALETTE["sb_card"])
        hdr.pack(fill="x", pady=(0, 5))

        pill = tk.Label(
            hdr,
            text=step_tag,
            bg="#1e293b",
            fg="#38bdf8",
            font=("Segoe UI", 7, "bold"),
            padx=5,
            pady=1,
        )
        pill.pack(side="left", padx=(0, 6))

        tk.Label(
            hdr,
            text=title,
            bg=PALETTE["sb_card"],
            fg="#f8fafc",
            font=("Segoe UI", 9, "bold"),
        ).pack(side="left")

        lbl_badge = tk.Label(
            hdr,
            text=badge_text,
            bg=badge_bg if badge_text else PALETTE["sb_card"],
            fg=badge_fg,
            font=("Segoe UI", 7, "bold"),
            padx=5 if badge_text else 0,
            pady=1,
        )
        lbl_badge.pack(side="right")
        return inner, lbl_badge

    def _build_sidebar_content(self) -> None:
        # Cabeçalho da Marca
        brand_row = tk.Frame(self.sidebar, bg=PALETTE["sb_bg"])
        brand_row.pack(fill="x", pady=(0, 10))
        if self.sidebar_logo_photo is not None:
            lbl_ico = tk.Label(brand_row, image=self.sidebar_logo_photo, bg=PALETTE["sb_bg"], bd=0)
            lbl_ico.pack(side="left", padx=(0, 9))
        title_box = tk.Frame(brand_row, bg=PALETTE["sb_bg"])
        title_box.pack(side="left", fill="x", expand=True)
        title_top = tk.Frame(title_box, bg=PALETTE["sb_bg"])
        title_top.pack(fill="x")
        tk.Label(
            title_top,
            text="ConfereFiscal",
            bg=PALETTE["sb_bg"],
            fg="#ffffff",
            font=("Segoe UI", 14, "bold"),
        ).pack(side="left")
        self.lbl_sidebar_version_badge = tk.Label(
            title_top,
            text=f"v{self.installed_version}",
            bg="#1e293b",
            fg="#38bdf8",
            font=("Segoe UI", 8, "bold"),
            padx=6,
            pady=1,
        )
        self.lbl_sidebar_version_badge.pack(side="left", padx=(8, 0))
        tk.Label(
            title_box,
            text="Auditoria e Conciliação Contábil (55 / 65)",
            bg=PALETTE["sb_bg"],
            fg="#94a3b8",
            font=("Segoe UI", 8),
        ).pack(anchor="w")

        # ---------------------------------------------------------------------
        # PASSO 1: CONEXÃO DO BANCO DE DADOS FIREBIRD (.FDB)
        # ---------------------------------------------------------------------
        c1, self.lbl_step1_badge = self._build_sidebar_card(
            "PASSO 1", "BANCO FIREBIRD (.FDB)", "PRONTO", "#064e3b", "#34d399"
        )

        r_hp = tk.Frame(c1, bg=PALETTE["sb_card"])
        r_hp.pack(fill="x", pady=2)
        tk.Label(r_hp, text="Host:", bg=PALETTE["sb_card"], fg=PALETTE["sb_muted"], font=("Segoe UI", 8), width=5, anchor="w").pack(side="left")
        self._create_sidebar_entry(r_hp, self.var_db_host, width=14).pack(side="left", padx=(0, 8))
        tk.Label(r_hp, text="Porta:", bg=PALETTE["sb_card"], fg=PALETTE["sb_muted"], font=("Segoe UI", 8), width=5, anchor="w").pack(side="left")
        self._create_sidebar_entry(r_hp, self.var_db_port, width=7, justify="center").pack(side="left")

        tk.Label(c1, text="Arquivo .FDB (Clipp 2.5 / 5.0):", bg=PALETTE["sb_card"], fg=PALETTE["sb_muted"], font=("Segoe UI", 8)).pack(anchor="w", pady=(3, 1))
        r_f = tk.Frame(c1, bg=PALETTE["sb_card"])
        r_f.pack(fill="x", pady=1)
        self._create_sidebar_entry(r_f, self.var_db_path, width=25).pack(side="left", fill="x", expand=True, padx=(0, 5))
        ModernButton(
            r_f, text="...", command=self.browse_sidebar_fdb,
            bg="#334155", hover_bg="#475569", pady=3, padx=8, font=("Segoe UI", 8, "bold")
        ).pack(side="right")

        r_up = tk.Frame(c1, bg=PALETTE["sb_card"])
        r_up.pack(fill="x", pady=3)
        tk.Label(r_up, text="Usuário:", bg=PALETTE["sb_card"], fg=PALETTE["sb_muted"], font=("Segoe UI", 8), width=7, anchor="w").pack(side="left")
        self._create_sidebar_entry(r_up, self.var_db_user, width=10).pack(side="left", padx=(0, 8))
        tk.Label(r_up, text="Senha:", bg=PALETTE["sb_card"], fg=PALETTE["sb_muted"], font=("Segoe UI", 8), width=6, anchor="w").pack(side="left")
        self._create_sidebar_entry(r_up, self.var_db_pass, width=9, show="*").pack(side="left")

        ModernButton(
            c1,
            text="Conectar e Sincronizar Base (.FDB)",
            command=self.connect_and_sync_clipp_database,
            bg="#1e293b",
            hover_bg="#334155",
            fg="#38bdf8",
            pady=5,
        ).pack(fill="x", pady=(4, 3))

        self.lbl_sidebar_db_status = tk.Label(
            c1,
            text="Base configurada. Aplique o período no Passo 2.",
            bg=PALETTE["sb_card"],
            fg="#94a3b8",
            font=("Segoe UI", 8),
            justify="left",
            wraplength=295,
        )
        self.lbl_sidebar_db_status.pack(anchor="w")

        # ---------------------------------------------------------------------
        # PASSO 2: FILTRO DE PERÍODO / COMPETÊNCIA (OBRIGATÓRIO)
        # ---------------------------------------------------------------------
        c2, self.lbl_step2_badge = self._build_sidebar_card(
            "PASSO 2", "FILTRO DE PERÍODO", "OBRIGATÓRIO", "#78350f", "#fbbf24"
        )

        tk.Label(
            c2,
            text="Informe o período para carregar os valores e liberar a importação dos XMLs:",
            bg=PALETTE["sb_card"],
            fg=PALETTE["sb_muted"],
            font=("Segoe UI", 8),
            justify="left",
            wraplength=295,
        ).pack(anchor="w", pady=(0, 4))

        r_dt = tk.Frame(c2, bg=PALETTE["sb_card"])
        r_dt.pack(fill="x", pady=2)
        tk.Label(r_dt, text="Início:", bg=PALETTE["sb_card"], fg="#e2e8f0", font=("Segoe UI", 8, "bold")).pack(side="left", padx=(0, 4))
        self.ent_dt_inicio = self._create_sidebar_entry(r_dt, self.var_dt_inicio, width=11, justify="center", font=("Segoe UI", 9, "bold"))
        self.ent_dt_inicio.pack(side="left", padx=(0, 8))
        self._bind_date_mask(self.ent_dt_inicio, self.var_dt_inicio)
        self.ent_dt_inicio.bind("<Return>", lambda e: self.apply_period_filter())

        tk.Label(r_dt, text="Fim:", bg=PALETTE["sb_card"], fg="#e2e8f0", font=("Segoe UI", 8, "bold")).pack(side="left", padx=(0, 4))
        self.ent_dt_fim = self._create_sidebar_entry(r_dt, self.var_dt_fim, width=11, justify="center", font=("Segoe UI", 9, "bold"))
        self.ent_dt_fim.pack(side="left")
        self._bind_date_mask(self.ent_dt_fim, self.var_dt_fim)
        self.ent_dt_fim.bind("<Return>", lambda e: self.apply_period_filter())

        tk.Label(c2, text="Critério Temporal do Confronto:", bg=PALETTE["sb_card"], fg=PALETTE["sb_muted"], font=("Segoe UI", 8)).pack(anchor="w", pady=(4, 1))
        rb1 = tk.Radiobutton(
            c2,
            text="Por Data de Emissão (tb_nfvenda.dt_emissao)",
            variable=self.var_date_base,
            value="issueDate",
            bg=PALETTE["sb_card"],
            fg="#f8fafc",
            selectcolor=PALETTE["sb_input_bg"],
            activebackground=PALETTE["sb_card"],
            activeforeground="#38bdf8",
            font=("Segoe UI", 8, "bold"),
            command=self._on_date_base_changed,
        )
        rb1.pack(anchor="w")
        rb2 = tk.Radiobutton(
            c2,
            text="Por Autorização SEFAZ (tb_nfe.data_hora)",
            variable=self.var_date_base,
            value="authDate",
            bg=PALETTE["sb_card"],
            fg="#f8fafc",
            selectcolor=PALETTE["sb_input_bg"],
            activebackground=PALETTE["sb_card"],
            activeforeground="#38bdf8",
            font=("Segoe UI", 8, "bold"),
            command=self._on_date_base_changed,
        )
        rb2.pack(anchor="w")

        btn_comp_row = tk.Frame(c2, bg=PALETTE["sb_card"])
        btn_comp_row.pack(fill="x", pady=(6, 2))
        self.btn_apply_period = ModernButton(
            btn_comp_row,
            text="✓ APLICAR FILTRO DE PERÍODO",
            command=self.apply_period_filter,
            bg=PALETTE["blue"],
            hover_bg=PALETTE["blue_hover"],
            pady=6,
            font=("Segoe UI", 9, "bold"),
        )
        self.btn_apply_period.pack(side="left", fill="x", expand=True, padx=(0, 5))

        ModernButton(
            btn_comp_row,
            text="Zerar",
            command=self.clear_period_filter,
            bg="#334155",
            hover_bg="#475569",
            pady=6,
            padx=10,
            font=("Segoe UI", 8, "bold"),
        ).pack(side="right")

        # ---------------------------------------------------------------------
        # PASSO 3: IMPORTAÇÃO DE ARQUIVOS (.ZIP, .RAR, .XML E .PDF)
        # ---------------------------------------------------------------------
        c3, self.lbl_step3_badge = self._build_sidebar_card(
            "PASSO 3", "IMPORTAR XMLs E PDF", "🔒 BLOQUEADO", "#334155", "#94a3b8"
        )

        self.lbl_step3_lock_hint = tk.Label(
            c3,
            text="⚠ Aplique o Filtro de Período no Passo 2 para habilitar a importação dos arquivos XML.",
            bg=PALETTE["sb_card"],
            fg="#fbbf24",
            font=("Segoe UI", 8, "bold"),
            justify="left",
            wraplength=295,
        )
        self.lbl_step3_lock_hint.pack(anchor="w", pady=(0, 5))

        self.btn_import_xml_files = ModernButton(
            c3,
            text="📂 Selecionar Arquivo .ZIP, .RAR ou .XML...",
            command=self.browse_and_import_xmls,
            bg=PALETTE["emerald"],
            hover_bg=PALETTE["emerald_hover"],
            pady=6,
            enabled=False,
            disabled_callback=self._warn_period_required,
        )
        self.btn_import_xml_files.pack(fill="x", pady=(0, 4))

        sub_xml_row = tk.Frame(c3, bg=PALETTE["sb_card"])
        sub_xml_row.pack(fill="x", pady=(0, 4))
        self.btn_import_xml_folder = ModernButton(
            sub_xml_row,
            text="📁 Selecionar Pasta de XMLs...",
            command=self.browse_and_import_xml_folder,
            bg="#0f766e",
            hover_bg="#115e59",
            pady=4,
            font=("Segoe UI", 8, "bold"),
            enabled=False,
            disabled_callback=self._warn_period_required,
        )
        self.btn_import_xml_folder.pack(side="left", fill="x", expand=True, padx=(0, 4))

        self.btn_clear_xmls = ModernButton(
            sub_xml_row,
            text="Limpar",
            command=self.clear_imported_xmls,
            bg="#334155",
            hover_bg="#475569",
            pady=4,
            padx=8,
            font=("Segoe UI", 8),
            enabled=False,
            disabled_callback=self._warn_period_required,
        )
        self.btn_clear_xmls.pack(side="right")

        self.lbl_sidebar_xml_status = tk.Label(
            c3,
            text="Nenhum lote XML (.zip/.rar) carregado.",
            bg=PALETTE["sb_card"],
            fg=PALETTE["sb_muted"],
            font=("Segoe UI", 8),
            justify="left",
            wraplength=295,
        )
        self.lbl_sidebar_xml_status.pack(anchor="w", pady=(0, 5))

        pdf_row = tk.Frame(c3, bg=PALETTE["sb_card"])
        pdf_row.pack(fill="x", pady=(1, 2))
        self.btn_import_pdf = ModernButton(
            pdf_row,
            text="📄 Selecionar Relatório PDF...",
            command=self.browse_and_import_pdf,
            bg=PALETTE["indigo"],
            hover_bg=PALETTE["indigo_hover"],
            pady=4,
            font=("Segoe UI", 8, "bold"),
            enabled=False,
            disabled_callback=self._warn_period_required,
        )
        self.btn_import_pdf.pack(side="left", fill="x", expand=True, padx=(0, 4))

        self.btn_clear_pdf = ModernButton(
            pdf_row,
            text="Limpar",
            command=self.clear_imported_pdf,
            bg="#334155",
            hover_bg="#475569",
            pady=4,
            padx=8,
            font=("Segoe UI", 8),
            enabled=False,
            disabled_callback=self._warn_period_required,
        )
        self.btn_clear_pdf.pack(side="right")

        self.lbl_sidebar_pdf_status = tk.Label(
            c3,
            text="Nenhum relatório PDF carregado (opcional).",
            bg=PALETTE["sb_card"],
            fg=PALETTE["sb_muted"],
            font=("Segoe UI", 8),
            justify="left",
            wraplength=295,
        )
        self.lbl_sidebar_pdf_status.pack(anchor="w")

        # ---------------------------------------------------------------------
        # PASSO 4: EXPORTAÇÃO DE RELATÓRIOS & UTILITÁRIOS
        # ---------------------------------------------------------------------
        c4, _ = self._build_sidebar_card("PASSO 4", "EXPORTAÇÃO & RELATÓRIOS")

        r_exp = tk.Frame(c4, bg=PALETTE["sb_card"])
        r_exp.pack(fill="x", pady=2)
        ModernButton(
            r_exp,
            text="📊 Exportar Excel (.xlsx)",
            command=self.export_xlsx_dialog,
            bg="#1e293b",
            hover_bg="#334155",
            fg="#38bdf8",
            pady=5,
            font=("Segoe UI", 8, "bold"),
        ).pack(side="left", fill="x", expand=True, padx=(0, 4))
        ModernButton(
            r_exp,
            text="📑 Exportar PDF",
            command=self.export_pdf_dialog,
            bg="#1e293b",
            hover_bg="#334155",
            fg="#38bdf8",
            pady=5,
            font=("Segoe UI", 8, "bold"),
        ).pack(side="left", fill="x", expand=True)

        r_aux = tk.Frame(c4, bg=PALETTE["sb_card"])
        r_aux.pack(fill="x", pady=(4, 0))
        self.btn_sidebar_update = ModernButton(
            r_aux,
            text=f"🔄 Atualizar (Instalada v{self.installed_version})",
            command=self.open_version_and_update_dialog,
            bg="#0284c7",
            hover_bg="#0369a1",
            fg="#ffffff",
            pady=5,
            font=("Segoe UI", 8, "bold"),
        )
        self.btn_sidebar_update.pack(fill="x", pady=(0, 4))

        ModernButton(
            r_aux,
            text="Abrir Interface no Navegador Web",
            command=self.start_and_open_web_ui,
            bg="#0f172a",
            hover_bg="#1e293b",
            fg="#94a3b8",
            pady=4,
            font=("Segoe UI", 8),
        ).pack(fill="x")

    # =========================================================================
    # BARRA DE STATUS SUPERIOR + 5 CARDS KPI EXECUTIVOS
    # =========================================================================

    def _build_top_header_banner(self) -> None:
        outer, inner = self._create_surface_card(self.workspace_area, padx=14, pady=8)
        outer.pack(fill="x", pady=(0, 10))

        self.lbl_top_workflow_badge = tk.Label(
            inner,
            text="AGUARDANDO FILTRO DE PERÍODO",
            bg="#fef3c7",
            fg="#92400e",
            font=("Segoe UI", 8, "bold"),
            padx=8,
            pady=3,
        )
        self.lbl_top_workflow_badge.pack(side="left", padx=(0, 10))

        self.lbl_overview_scope = tk.Label(
            inner,
            text="Valores iniciados zerados (R$ 0,00). Informe a Data Início e a Data Fim no Passo 2 e clique em 'Aplicar Filtro de Período'.",
            bg=PALETTE["card_bg"],
            fg=PALETTE["text_primary"],
            font=("Segoe UI", 9, "bold"),
        )
        self.lbl_overview_scope.pack(side="left")

        # Bloco Direito: Versão Instalada vs Publicada + Botão Atualizar
        ver_box = tk.Frame(inner, bg=PALETTE["card_bg"])
        ver_box.pack(side="right")

        self.lbl_top_version_info = tk.Label(
            ver_box,
            text=f"Instalada: v{self.installed_version}",
            bg="#f1f5f9",
            fg=PALETTE["text_secondary"],
            font=("Segoe UI", 8, "bold"),
            padx=8,
            pady=4,
        )
        self.lbl_top_version_info.pack(side="left", padx=(0, 6))

        self.btn_check_update = ModernButton(
            ver_box,
            text="🔄 Atualizar",
            command=self.open_version_and_update_dialog,
            bg=PALETTE["sky"],
            hover_bg=PALETTE["sky_hover"],
            fg="#ffffff",
            padx=12,
            pady=4,
            font=("Segoe UI", 8, "bold"),
        )
        self.btn_check_update.pack(side="left")

    def _build_top_kpi_bar(self) -> None:
        kpi_bar = tk.Frame(self.workspace_area, bg=PALETTE["app_bg"])
        kpi_bar.pack(fill="x", pady=(0, 10))
        for i in range(5):
            kpi_bar.columnconfigure(i, weight=1, uniform="kpi")

        def _make_kpi(col: int, title: str, accent_color: str):
            outer, inner = self._create_surface_card(kpi_bar, accent_top=accent_color, padx=12, pady=9)
            outer.grid(row=0, column=col, sticky="nsew", padx=(0 if col == 0 else 8, 0))
            tk.Label(
                inner,
                text=title,
                bg=PALETTE["card_bg"],
                fg=PALETTE["text_secondary"],
                font=("Segoe UI", 8, "bold"),
            ).pack(anchor="w")
            lbl_val = tk.Label(
                inner,
                text="R$ 0,00",
                bg=PALETTE["card_bg"],
                fg=PALETTE["text_primary"],
                font=("Segoe UI", 14, "bold"),
            )
            lbl_val.pack(anchor="w", pady=(3, 2))
            lbl_sub = tk.Label(
                inner,
                text="0 notas (Aplique o período)",
                bg=PALETTE["card_bg"],
                fg=PALETTE["text_muted"],
                font=("Segoe UI", 8),
            )
            lbl_sub.pack(anchor="w")
            return lbl_val, lbl_sub

        self.kpi_bco_emi_val, self.kpi_bco_emi_sub = _make_kpi(0, "TOTAL BANCO (POR EMISSÃO)", PALETTE["blue"])
        self.kpi_bco_aut_val, self.kpi_bco_aut_sub = _make_kpi(1, "TOTAL BANCO (POR AUTORIZAÇÃO)", PALETTE["sky"])
        self.kpi_xml_val, self.kpi_xml_sub = _make_kpi(2, "TOTAL XMLs IMPORTADOS (.ZIP/.RAR)", PALETTE["indigo"])
        self.kpi_pdf_val, self.kpi_pdf_sub = _make_kpi(3, "TOTAL RELATÓRIO PDF", PALETTE["amber"])
        self.kpi_bat_val, self.kpi_bat_sub = _make_kpi(4, "STATUS DE BATIMENTO", PALETTE["emerald"])

    # =========================================================================
    # BARRA DE NAVEGAÇÃO MODERNA E PÁGINAS DE CONTEÚDO
    # =========================================================================

    def _build_navigation_tabs_and_pages(self) -> None:
        nav_outer = tk.Frame(self.workspace_area, bg=PALETTE["card_border"], padx=1, pady=1)
        nav_outer.pack(fill="x", pady=(0, 8))
        nav_bar = tk.Frame(nav_outer, bg=PALETTE["card_bg"], padx=6, pady=4)
        nav_bar.pack(fill="x")

        tabs_spec = [
            ("overview", "1. Visão Geral & Comparativo Triplo"),
            ("nfe55", "2. Conciliação NF-e (Modelo 55)"),
            ("nfce65", "3. Conciliação NFC-e (Modelo 65)"),
            ("events", "4. Canceladas e Inutilizadas (101/102)"),
            ("divergences", "5. Divergências e Faltantes"),
            ("sql", "6. Console SQL (Banco)"),
        ]

        for key, label in tabs_spec:
            btn_frame = tk.Frame(nav_bar, bg=PALETTE["card_bg"], cursor="hand2")
            btn_frame.pack(side="left", padx=3)
            lbl = tk.Label(
                btn_frame,
                text=label,
                bg=PALETTE["card_bg"],
                fg=PALETTE["text_secondary"],
                font=("Segoe UI", 9, "bold"),
                padx=12,
                pady=6,
                cursor="hand2",
            )
            lbl.pack(fill="x")
            indicator = tk.Frame(btn_frame, bg=PALETTE["card_bg"], height=3)
            indicator.pack(fill="x", side="bottom")

            for w in (btn_frame, lbl, indicator):
                w.bind("<Button-1>", lambda _e, k=key: self.switch_main_tab(k))

            self.tab_buttons[key] = (btn_frame, lbl, indicator)

        # Container das Páginas
        self.pages_container = tk.Frame(self.workspace_area, bg=PALETTE["app_bg"])
        self.pages_container.pack(fill="both", expand=True)

        for key, _ in tabs_spec:
            frame = tk.Frame(self.pages_container, bg=PALETTE["app_bg"])
            self.tab_frames[key] = frame

        self._build_tab_overview(self.tab_frames["overview"])
        self._build_tab_model(self.tab_frames["nfe55"], "55", "NF-e Modelo 55")
        self._build_tab_model(self.tab_frames["nfce65"], "65", "NFC-e Modelo 65")
        self._build_tab_events(self.tab_frames["events"])
        self._build_tab_divergences(self.tab_frames["divergences"])
        self._build_tab_sql_and_local_db(self.tab_frames["sql"])

        self.switch_main_tab("overview")

    def switch_main_tab(self, tab_key: str) -> None:
        self.active_tab_key = tab_key
        for k, (btn_f, lbl, ind) in self.tab_buttons.items():
            if k == tab_key:
                btn_f.configure(bg=PALETTE["blue_light"])
                lbl.configure(bg=PALETTE["blue_light"], fg=PALETTE["blue"])
                ind.configure(bg=PALETTE["blue"])
            else:
                btn_f.configure(bg=PALETTE["card_bg"])
                lbl.configure(bg=PALETTE["card_bg"], fg=PALETTE["text_secondary"])
                ind.configure(bg=PALETTE["card_bg"])

        for k, frame in self.tab_frames.items():
            if k == tab_key:
                frame.pack(fill="both", expand=True)
            else:
                frame.pack_forget()

    # -------------------------------------------------------------------------
    # PÁGINA 1: VISÃO GERAL, COMPARATIVO TRIPLO E DESAFIO TEMPORAL D+1
    # -------------------------------------------------------------------------
    def _build_tab_overview(self, parent: tk.Frame) -> None:
        tc_outer, tc_inner = self._create_surface_card(parent, padx=12, pady=10)
        tc_outer.pack(fill="x", pady=(0, 10))

        tk.Label(
            tc_inner,
            text="Comparativo Triplo por Modelo Fiscal: Banco (tb_nfvenda / tb_nfe) vs. Lote XML (.zip/.rar) vs. Relatório PDF",
            bg=PALETTE["card_bg"],
            fg=PALETTE["text_primary"],
            font=("Segoe UI", 10, "bold"),
        ).pack(anchor="w", pady=(0, 8))

        cols_tc = (
            "label", "qtdBcoEmi", "totBcoEmi", "qtdBcoAut", "totBcoAut",
            "divD1", "qtdXml", "totXml", "qtdPdf", "totPdf", "difBcoXml", "status",
        )
        self.tree_triple = ttk.Treeview(tc_inner, columns=cols_tc, show="headings", height=4, style="Executive.Treeview")
        for c, lbl, w, anc in [
            ("label", "Modelo Fiscal", 180, "w"),
            ("qtdBcoEmi", "Notas Bco (Emi)", 95, "center"),
            ("totBcoEmi", "Total Banco Emissão", 125, "e"),
            ("qtdBcoAut", "Notas Bco (Aut)", 95, "center"),
            ("totBcoAut", "Total Banco Autoriz.", 125, "e"),
            ("divD1", "Diferença D+1", 105, "e"),
            ("qtdXml", "Qtd XML", 75, "center"),
            ("totXml", "Total XML (.zip/.rar)", 125, "e"),
            ("qtdPdf", "Qtd PDF", 75, "center"),
            ("totPdf", "Total PDF", 110, "e"),
            ("difBcoXml", "Dif. Banco vs XML", 115, "e"),
            ("status", "Situação da Conciliação", 180, "w"),
        ]:
            self.tree_triple.heading(c, text=lbl)
            self.tree_triple.column(c, width=w, anchor=anc)
        self._configure_tree_tags(self.tree_triple)
        self.tree_triple.pack(fill="x")

        # Desafio Temporal D+1 (Emissão != Autorização SEFAZ)
        d1_outer, d1_inner = self._create_surface_card(parent, padx=12, pady=10)
        d1_outer.pack(fill="both", expand=True)

        self.lbl_d1_header = tk.Label(
            d1_inner,
            text="Desafio Temporal (D+1 / Contingência): Notas emitidas em um dia e autorizadas na SEFAZ em dia posterior",
            bg=PALETTE["card_bg"],
            fg=PALETTE["text_primary"],
            font=("Segoe UI", 10, "bold"),
        )
        self.lbl_d1_header.pack(anchor="w", pady=(0, 8))

        cols_d1 = ("nf_numero", "nf_modelo", "nf_serie", "id_nfe", "dt_emissao", "data_hora_sefaz", "status_des", "valor")
        self.tree_d1 = ttk.Treeview(d1_inner, columns=cols_d1, show="headings", height=12, style="Executive.Treeview")
        for c, lbl, w, anc in [
            ("nf_numero", "Nº Nota (nf_numero)", 110, "center"),
            ("nf_modelo", "Modelo", 65, "center"),
            ("nf_serie", "Série", 55, "center"),
            ("id_nfe", "Chave de Acesso 44 dígitos (id_nfe)", 315, "w"),
            ("dt_emissao", "Data Emissão (tb_nfvenda)", 140, "center"),
            ("data_hora_sefaz", "Autorização SEFAZ (tb_nfe)", 165, "center"),
            ("status_des", "Status SEFAZ (status_des)", 220, "w"),
            ("valor", "Valor da Nota", 120, "e"),
        ]:
            self.tree_d1.heading(c, text=lbl)
            self.tree_d1.column(c, width=w, anchor=anc)
        self._configure_tree_tags(self.tree_d1)
        vsb = ttk.Scrollbar(d1_inner, orient="vertical", command=self.tree_d1.yview)
        self.tree_d1.configure(yscrollcommand=vsb.set)
        self.tree_d1.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")

    # -------------------------------------------------------------------------
    # PÁGINAS 2 E 3: CONCILIAÇÃO NF-e (55) E NFC-e (65) — LARGURA 100% EM SUB-ABAS
    # -------------------------------------------------------------------------
    def _build_tab_model(self, parent: tk.Frame, model_code: str, model_label: str) -> None:
        top_outer, top_inner = self._create_surface_card(parent, padx=12, pady=8, bg=PALETTE["card_sub_bg"])
        top_outer.pack(fill="x", pady=(0, 8))

        lbl_summary = tk.Label(
            top_inner,
            text=f"{model_label}: Valores zerados (R$ 0,00). Aplique o filtro de período no Passo 2.",
            bg=PALETTE["card_sub_bg"],
            fg=PALETTE["text_primary"],
            font=("Segoe UI", 9, "bold"),
        )
        lbl_summary.pack(side="left")

        # Seletor moderno de Sub-Visões (100% da largura — nunca corta colunas!)
        sub_bar_outer = tk.Frame(parent, bg=PALETTE["card_border"], padx=1, pady=1)
        sub_bar_outer.pack(fill="x", pady=(0, 8))
        sub_bar = tk.Frame(sub_bar_outer, bg=PALETTE["card_bg"], padx=6, pady=4)
        sub_bar.pack(fill="x")

        sub_pages_holder = tk.Frame(parent, bg=PALETTE["app_bg"])
        sub_pages_holder.pack(fill="both", expand=True)

        sub_frames: Dict[str, tk.Frame] = {}
        sub_labels: Dict[str, tk.Label] = {}

        sub_specs = [
            ("cfop", f"📊 1. Tabela Dinâmica por CFOP ({model_label})"),
            ("ncm", f"🏷️ 2. Tabela Dinâmica por NCM ({model_label})"),
            ("docs", f"📑 3. Relação de Notas Fiscais — {model_label} (Status 100)"),
        ]

        def _switch_sub(sub_key: str) -> None:
            for sk, sf in sub_frames.items():
                if sk == sub_key:
                    sf.pack(fill="both", expand=True)
                    sub_labels[sk].configure(bg=PALETTE["blue"], fg="#ffffff")
                else:
                    sf.pack_forget()
                    sub_labels[sk].configure(bg="#f1f5f9", fg=PALETTE["text_secondary"])

        for sk, slbl in sub_specs:
            lbl_btn = tk.Label(
                sub_bar,
                text=slbl,
                bg="#f1f5f9",
                fg=PALETTE["text_secondary"],
                font=("Segoe UI", 9, "bold"),
                padx=14,
                pady=5,
                cursor="hand2",
            )
            lbl_btn.pack(side="left", padx=4)
            lbl_btn.bind("<Button-1>", lambda _e, k=sk: _switch_sub(k))
            sub_labels[sk] = lbl_btn

            s_outer, s_inner = self._create_surface_card(sub_pages_holder, padx=12, pady=10)
            sub_frames[sk] = s_outer
            if sk == "cfop":
                sub_cfop = s_inner
            elif sk == "ncm":
                sub_ncm = s_inner
            else:
                sub_docs = s_inner

        # --- SUB-VISÃO 1: TABELA DINÂMICA POR CFOP (LARGURA TOTAL) ---
        tk.Label(
            sub_cfop,
            text=f"Agrupamento Fiscal por CFOP — {model_label}: Somatório de Base de Cálculo ICMS, Valor ICMS e Valor Contábil (Banco vs. XML)",
            bg=PALETTE["card_bg"],
            fg=PALETTE["text_primary"],
            font=("Segoe UI", 10, "bold"),
        ).pack(anchor="w", pady=(0, 8))

        cols_cfop = (
            "cfop", "desc_cfop", "qtdBco", "bcBco", "icmsBco", "valBco",
            "qtdXml", "bcXml", "icmsXml", "valXml", "dif", "status",
        )
        tree_cfop = ttk.Treeview(sub_cfop, columns=cols_cfop, show="headings", height=16, style="Executive.Treeview")
        for c, lbl, w, anc in [
            ("cfop", "CFOP", 65, "center"),
            ("desc_cfop", "Descrição da Operação Fiscal", 230, "w"),
            ("qtdBco", "Notas (Banco)", 90, "center"),
            ("bcBco", "Base ICMS (Banco)", 115, "e"),
            ("icmsBco", "Vlr ICMS (Banco)", 110, "e"),
            ("valBco", "Valor Contábil (Banco)", 130, "e"),
            ("qtdXml", "Notas (XML)", 85, "center"),
            ("bcXml", "Base ICMS (XML)", 115, "e"),
            ("icmsXml", "Vlr ICMS (XML)", 105, "e"),
            ("valXml", "Valor Contábil (XML)", 130, "e"),
            ("dif", "Diferença (R$)", 110, "e"),
            ("status", "Situação", 100, "center"),
        ]:
            tree_cfop.heading(c, text=lbl)
            tree_cfop.column(c, width=w, anchor=anc)
        self._configure_tree_tags(tree_cfop)
        vsb_cfop = ttk.Scrollbar(sub_cfop, orient="vertical", command=tree_cfop.yview)
        tree_cfop.configure(yscrollcommand=vsb_cfop.set)
        tree_cfop.pack(side="left", fill="both", expand=True)
        vsb_cfop.pack(side="right", fill="y")

        # --- SUB-VISÃO 2: TABELA DINÂMICA POR NCM (LARGURA TOTAL) ---
        tk.Label(
            sub_ncm,
            text=f"Agrupamento Fiscal por NCM — {model_label}: Somatório de Base de Cálculo e Valor Contábil por Classificação NCM",
            bg=PALETTE["card_bg"],
            fg=PALETTE["text_primary"],
            font=("Segoe UI", 10, "bold"),
        ).pack(anchor="w", pady=(0, 8))

        cols_ncm = (
            "ncm", "descricao", "qtdBco", "bcBco", "valBco",
            "qtdXml", "bcXml", "valXml", "dif", "status",
        )
        tree_ncm = ttk.Treeview(sub_ncm, columns=cols_ncm, show="headings", height=16, style="Executive.Treeview")
        for c, lbl, w, anc in [
            ("ncm", "Código NCM", 95, "center"),
            ("descricao", "Descrição do Produto / Mercadoria", 280, "w"),
            ("qtdBco", "Itens (Banco)", 95, "center"),
            ("bcBco", "Base ICMS (Banco)", 125, "e"),
            ("valBco", "Valor Contábil (Banco)", 135, "e"),
            ("qtdXml", "Itens (XML)", 90, "center"),
            ("bcXml", "Base ICMS (XML)", 125, "e"),
            ("valXml", "Valor Contábil (XML)", 135, "e"),
            ("dif", "Diferença (R$)", 115, "e"),
            ("status", "Situação", 100, "center"),
        ]:
            tree_ncm.heading(c, text=lbl)
            tree_ncm.column(c, width=w, anchor=anc)
        self._configure_tree_tags(tree_ncm)
        vsb_ncm = ttk.Scrollbar(sub_ncm, orient="vertical", command=tree_ncm.yview)
        tree_ncm.configure(yscrollcommand=vsb_ncm.set)
        tree_ncm.pack(side="left", fill="both", expand=True)
        vsb_ncm.pack(side="right", fill="y")

        # --- SUB-VISÃO 3: RELAÇÃO DE DOCUMENTOS DO MODELO ---
        filter_bar = tk.Frame(sub_docs, bg=PALETTE["card_bg"])
        filter_bar.pack(fill="x", pady=(0, 8))
        tk.Label(
            filter_bar,
            text=f"Relação de Notas Autorizadas (Status 100) — {model_label}:",
            bg=PALETTE["card_bg"],
            fg=PALETTE["text_primary"],
            font=("Segoe UI", 10, "bold"),
        ).pack(side="left")

        search_var = self.var_search_55 if model_code == "55" else self.var_search_65
        ModernButton(
            filter_bar,
            text="Limpar Busca",
            command=lambda sv=search_var, mc=model_code: (sv.set(""), self._populate_model_tab(mc, self.last_db_audit_data)),
            bg="#e2e8f0",
            hover_bg="#cbd5e1",
            fg=PALETTE["text_primary"],
            pady=3,
            padx=8,
            font=("Segoe UI", 8, "bold"),
        ).pack(side="right", padx=(4, 0))

        ent_s = tk.Entry(
            filter_bar,
            textvariable=search_var,
            width=28,
            bg="#f8fafc",
            fg=PALETTE["text_primary"],
            relief="flat",
            bd=4,
            highlightthickness=1,
            highlightbackground="#cbd5e1",
            highlightcolor=PALETTE["blue"],
            font=("Segoe UI", 9),
        )
        ent_s.pack(side="right", padx=6)
        ent_s.bind("<KeyRelease>", lambda _e, mc=model_code: self._populate_model_tab(mc, self.last_db_audit_data))
        tk.Label(filter_bar, text="Filtrar por Nº ou Chave:", bg=PALETTE["card_bg"], fg=PALETTE["text_muted"], font=("Segoe UI", 9)).pack(side="right")

        cols_doc = (
            "nf_numero", "nf_serie", "id_nfe", "dt_emissao", "data_hora_sefaz",
            "cfop", "base_icms", "valor_icms", "valor", "status_conciliacao",
        )
        tree_docs = ttk.Treeview(sub_docs, columns=cols_doc, show="headings", height=15, style="Executive.Treeview")
        for c, lbl, w, anc in [
            ("nf_numero", "Nº Nota (nf_numero)", 110, "center"),
            ("nf_serie", "Série", 55, "center"),
            ("id_nfe", "Chave de Acesso 44 dígitos (id_nfe)", 305, "w"),
            ("dt_emissao", "Data Emissão", 100, "center"),
            ("data_hora_sefaz", "Autorização SEFAZ", 145, "center"),
            ("cfop", "CFOP", 65, "center"),
            ("base_icms", "Base ICMS", 105, "e"),
            ("valor_icms", "Vlr ICMS", 95, "e"),
            ("valor", "Valor Total", 110, "e"),
            ("status_conciliacao", "Confronto com XML", 180, "w"),
        ]:
            tree_docs.heading(c, text=lbl)
            tree_docs.column(c, width=w, anchor=anc)
        self._configure_tree_tags(tree_docs)
        vsb_docs = ttk.Scrollbar(sub_docs, orient="vertical", command=tree_docs.yview)
        tree_docs.configure(yscrollcommand=vsb_docs.set)
        tree_docs.pack(side="left", fill="both", expand=True)
        vsb_docs.pack(side="right", fill="y")

        _switch_sub("cfop")

        if model_code == "55":
            self.lbl_sum_55 = lbl_summary
            self.tree_cfop_55 = tree_cfop
            self.tree_ncm_55 = tree_ncm
            self.tree_docs_55 = tree_docs
        else:
            self.lbl_sum_65 = lbl_summary
            self.tree_cfop_65 = tree_cfop
            self.tree_ncm_65 = tree_ncm
            self.tree_docs_65 = tree_docs

    # -------------------------------------------------------------------------
    # PÁGINA 4: CANCELADAS (STATUS 101) E INUTILIZADAS (STATUS 102)
    # -------------------------------------------------------------------------
    def _build_tab_events(self, parent: tk.Frame) -> None:
        h_outer, h_inner = self._create_surface_card(parent, padx=12, pady=10)
        h_outer.pack(fill="x", pady=(0, 8))

        self.lbl_events_summary = tk.Label(
            h_inner,
            text="Aba Exclusiva: Canceladas (Status 101) e Inutilizadas (Status 102) — Isoladas do Faturamento Líquido (Status 100)",
            bg=PALETTE["card_bg"],
            fg=PALETTE["text_primary"],
            font=("Segoe UI", 10, "bold"),
        )
        self.lbl_events_summary.pack(side="left")

        filter_box = tk.Frame(h_inner, bg=PALETTE["card_bg"])
        filter_box.pack(side="right")
        for lbl, val in [
            ("Todos Eventos (101 + 102)", "ALL"),
            ("Somente Canceladas (101)", "101"),
            ("Somente Inutilizadas (102)", "102"),
        ]:
            tk.Radiobutton(
                filter_box,
                text=lbl,
                variable=self.var_events_filter,
                value=val,
                bg=PALETTE["card_bg"],
                fg=PALETTE["text_primary"],
                selectcolor="#f1f5f9",
                activebackground=PALETTE["card_bg"],
                font=("Segoe UI", 9),
                command=self._populate_events_tree,
            ).pack(side="left", padx=6)

        g_outer, g_inner = self._create_surface_card(parent, padx=12, pady=10)
        g_outer.pack(fill="both", expand=True)

        cols_ev = (
            "status_codigo", "status", "nf_numero", "nf_serie", "nf_modelo",
            "id_nfe", "dt_emissao", "data_hora_sefaz", "status_des", "valor", "fontes",
        )
        self.tree_events = ttk.Treeview(g_inner, columns=cols_ev, show="headings", height=18, style="Executive.Treeview")
        for c, lbl, w, anc in [
            ("status_codigo", "Cód.", 55, "center"),
            ("status", "Tipo Evento", 95, "center"),
            ("nf_numero", "nf_numero", 85, "center"),
            ("nf_serie", "Série", 50, "center"),
            ("nf_modelo", "Mod", 50, "center"),
            ("id_nfe", "id_nfe (Chave de Acesso 44 dígitos)", 295, "w"),
            ("dt_emissao", "dt_emissao", 95, "center"),
            ("data_hora_sefaz", "data_hora SEFAZ", 145, "center"),
            ("status_des", "status_des (Motivo / Protocolo)", 220, "w"),
            ("valor", "Valor (R$)", 100, "e"),
            ("fontes", "Presença nas Fontes", 145, "center"),
        ]:
            self.tree_events.heading(c, text=lbl)
            self.tree_events.column(c, width=w, anchor=anc)
        self._configure_tree_tags(self.tree_events)
        vsb = ttk.Scrollbar(g_inner, orient="vertical", command=self.tree_events.yview)
        self.tree_events.configure(yscrollcommand=vsb.set)
        self.tree_events.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")

    # -------------------------------------------------------------------------
    # PÁGINA 5: MATRIZ DE DIVERGÊNCIAS & AUDITORIA DE CHAVES
    # -------------------------------------------------------------------------
    def _build_tab_divergences(self, parent: tk.Frame) -> None:
        t_outer, t_inner = self._create_surface_card(parent, padx=12, pady=10)
        t_outer.pack(fill="x", pady=(0, 8))

        head_row = tk.Frame(t_inner, bg=PALETTE["card_bg"])
        head_row.pack(fill="x", pady=(0, 6))
        self.lbl_matrix_summary = tk.Label(
            head_row,
            text="Matriz de Divergências & Auditoria de Chaves (nf_numero, id_nfe, dt_emissao, data_hora_sefaz, status_des, Valor, Origem da Falha)",
            bg=PALETTE["card_bg"],
            fg=PALETTE["text_primary"],
            font=("Segoe UI", 10, "bold"),
        )
        self.lbl_matrix_summary.pack(side="left")

        ent_ms = tk.Entry(
            head_row,
            textvariable=self.var_matrix_search,
            width=26,
            bg="#f8fafc",
            fg=PALETTE["text_primary"],
            relief="flat",
            bd=4,
            highlightthickness=1,
            highlightbackground="#cbd5e1",
            highlightcolor=PALETTE["blue"],
            font=("Segoe UI", 9),
        )
        ent_ms.pack(side="right", padx=4)
        ent_ms.bind("<KeyRelease>", lambda _e: self._populate_divergence_matrix_tree())
        tk.Label(head_row, text="Buscar Nº ou Chave:", bg=PALETTE["card_bg"], fg=PALETTE["text_muted"], font=("Segoe UI", 9)).pack(side="right")

        f_row = tk.Frame(t_inner, bg=PALETTE["card_bg"])
        f_row.pack(fill="x")
        for lbl, code in [
            ("Todas Divergências", "ALL"),
            ("Consta no Relatório mas NÃO no XML", "RELATORIO_SEM_XML"),
            ("Consta no XML mas NÃO no Relatório/Banco", "XML_SEM_RELATORIO_OU_BANCO"),
            ("Consta no Banco mas sem XML", "BANCO_SEM_XML"),
            ("Desafio Temporal D+1", "DIVERGENCIA_D_MAIS_1"),
            ("Divergência de Valor / Chave", "OUTRAS"),
        ]:
            tk.Radiobutton(
                f_row,
                text=lbl,
                variable=self.var_matrix_filter,
                value=code,
                bg=PALETTE["card_bg"],
                fg=PALETTE["text_primary"],
                selectcolor="#f1f5f9",
                activebackground=PALETTE["card_bg"],
                font=("Segoe UI", 8, "bold"),
                command=self._populate_divergence_matrix_tree,
            ).pack(side="left", padx=5)

        g_outer, g_inner = self._create_surface_card(parent, padx=12, pady=10)
        g_outer.pack(fill="both", expand=True)

        cols_mv = (
            "nf_numero", "nf_modelo", "id_nfe", "dt_emissao",
            "data_hora_sefaz", "status_des", "valor", "origem_falha",
        )
        self.tree_matrix = ttk.Treeview(g_inner, columns=cols_mv, show="headings", height=18, style="Executive.Treeview")
        for c, lbl, w, anc in [
            ("nf_numero", "nf_numero", 85, "center"),
            ("nf_modelo", "Mod", 50, "center"),
            ("id_nfe", "id_nfe (Chave 44 dígitos)", 295, "w"),
            ("dt_emissao", "dt_emissao", 95, "center"),
            ("data_hora_sefaz", "data_hora_sefaz", 145, "center"),
            ("status_des", "status_des", 195, "w"),
            ("valor", "Valor (R$)", 100, "e"),
            ("origem_falha", "Origem da Falha", 365, "w"),
        ]:
            self.tree_matrix.heading(c, text=lbl)
            self.tree_matrix.column(c, width=w, anchor=anc)
        self._configure_tree_tags(self.tree_matrix)
        vsb = ttk.Scrollbar(g_inner, orient="vertical", command=self.tree_matrix.yview)
        self.tree_matrix.configure(yscrollcommand=vsb.set)
        self.tree_matrix.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")

    # -------------------------------------------------------------------------
    # PÁGINA 6: CONSOLE SQL & INSPEÇÃO DA BASE LOCAL
    # -------------------------------------------------------------------------
    def _build_tab_sql_and_local_db(self, parent: tk.Frame) -> None:
        s_outer, s_inner = self._create_surface_card(parent, padx=12, pady=10)
        s_outer.pack(fill="both", expand=True)

        top_sql = tk.Frame(s_inner, bg=PALETTE["card_bg"])
        top_sql.pack(fill="x")
        tk.Label(
            top_sql,
            text="Console SQL Somente-Leitura (Consulte tb_nfvenda, tb_nfe, tb_nfv_item, VW_AUDITORIA_CLIPP):",
            bg=PALETTE["card_bg"],
            fg=PALETTE["text_primary"],
            font=("Segoe UI", 10, "bold"),
        ).pack(side="left")

        ModernButton(
            top_sql,
            text="▶ Executar SELECT",
            command=self.run_sql_console_query,
            bg=PALETTE["blue"],
            hover_bg=PALETTE["blue_hover"],
            pady=4,
            padx=12,
            font=("Segoe UI", 8, "bold"),
        ).pack(side="right", padx=3)

        for lbl, q in [
            (
                "Total por Modelo (55/65)",
                "SELECT NF_MODELO, COUNT(*) AS QTD_NOTAS, ROUND(SUM(VALOR_TOTAL), 2) AS TOTAL_VENDA "
                "FROM VW_AUDITORIA_CLIPP WHERE STATUS_CODIGO = '100' GROUP BY NF_MODELO",
            ),
            (
                "Total por CFOP",
                "SELECT MODELO, CFOP, COUNT(DISTINCT ID_NFVENDA) AS QTD_NOTAS, "
                "ROUND(SUM(VLR_BC_ICMS), 2) AS BASE_ICMS, ROUND(SUM(VLR_ICMS), 2) AS VALOR_ICMS, "
                "ROUND(SUM(VLR_CONTABIL), 2) AS VALOR_CONTABIL "
                "FROM TB_NFV_ITEM GROUP BY MODELO, CFOP ORDER BY MODELO, CFOP",
            ),
            (
                "Divergências D+1",
                "SELECT NF_NUMERO, NF_MODELO, ID_NFE, DT_EMISSAO, DATA_HORA_SEFAZ, STATUS_DES, VALOR_TOTAL "
                "FROM VW_AUDITORIA_CLIPP WHERE DT_EMISSAO <> DT_AUTORIZACAO AND DT_AUTORIZACAO <> '' ORDER BY DT_EMISSAO DESC",
            ),
        ]:
            ModernButton(
                top_sql,
                text=lbl,
                command=lambda sql_q=q: self.set_and_run_sql(sql_q),
                bg="#e2e8f0",
                hover_bg="#cbd5e1",
                fg=PALETTE["text_primary"],
                pady=4,
                padx=8,
                font=("Segoe UI", 8, "bold"),
            ).pack(side="right", padx=3)

        self.txt_sql = tk.Text(
            s_inner,
            height=4,
            font=("Consolas", 10),
            bg="#0f172a",
            fg="#f8fafc",
            insertbackground="#38bdf8",
            relief="flat",
            padx=8,
            pady=6,
        )
        self.txt_sql.pack(fill="x", pady=8)
        self.txt_sql.insert(
            "1.0",
            "SELECT FIRST 50 NF_NUMERO, NF_MODELO, ID_NFE, DT_EMISSAO, DATA_HORA_SEFAZ, STATUS_CODIGO, STATUS_DES, VALOR_TOTAL FROM VW_AUDITORIA_CLIPP ORDER BY DT_EMISSAO DESC",
        )

        self.tree_sql = ttk.Treeview(s_inner, show="headings", height=16, style="Executive.Treeview")
        self._configure_tree_tags(self.tree_sql)
        vsb = ttk.Scrollbar(s_inner, orient="vertical", command=self.tree_sql.yview)
        self.tree_sql.configure(yscrollcommand=vsb.set)
        self.tree_sql.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")

    # =========================================================================
    # REGRAS DE BLOQUEIO / LIBERAÇÃO POR PERÍODO E AÇÕES DO USUÁRIO
    # =========================================================================

    def _warn_period_required(self) -> None:
        messagebox.showwarning(
            "Filtro de Período Obrigatório (Passo 2)",
            "Para habilitar a importação dos arquivos XML (.zip, .rar, .xml) e realizar a comparação correta:\n\n"
            "1. Informe a Data Inicial (Início) e a Data Final (Fim) no PASSO 2 (ex.: 01/10/2026 a 31/10/2026).\n"
            "2. Clique no botão '✓ APLICAR FILTRO DE PERÍODO'.",
        )
        self.ent_dt_inicio.focus_set()

    def _update_step2_and_step3_lock_ui(self) -> None:
        if self.period_filter_applied and self.applied_dt_inicio_iso and self.applied_dt_fim_iso:
            br_ini = _fmt_date_br(self.applied_dt_inicio_iso)
            br_fim = _fmt_date_br(self.applied_dt_fim_iso)

            self.lbl_step2_badge.configure(text="✓ ATIVO", bg="#064e3b", fg="#34d399")
            self.lbl_step3_badge.configure(text="🔓 LIBERADO", bg="#064e3b", fg="#34d399")
            self.lbl_step3_lock_hint.configure(
                text=f"✓ Período {br_ini} a {br_fim} ativo. Importação de XMLs e PDF habilitada!",
                fg="#34d399",
            )
            self.btn_import_xml_files.set_enabled(True)
            self.btn_import_xml_folder.set_enabled(True)
            self.btn_clear_xmls.set_enabled(True)
            self.btn_import_pdf.set_enabled(True)
            self.btn_clear_pdf.set_enabled(True)
        else:
            self.lbl_step2_badge.configure(text="OBRIGATÓRIO", bg="#78350f", fg="#fbbf24")
            self.lbl_step3_badge.configure(text="🔒 BLOQUEADO", bg="#334155", fg="#94a3b8")
            self.lbl_step3_lock_hint.configure(
                text="⚠ Aplique o Filtro de Período no Passo 2 para habilitar a importação dos arquivos XML.",
                fg="#fbbf24",
            )
            self.btn_import_xml_files.set_enabled(False)
            self.btn_import_xml_folder.set_enabled(False)
            self.btn_clear_xmls.set_enabled(False)
            self.btn_import_pdf.set_enabled(False)
            self.btn_clear_pdf.set_enabled(False)

    def apply_period_filter(self) -> None:
        raw_ini = self.var_dt_inicio.get().strip()
        raw_fim = self.var_dt_fim.get().strip()
        dt_ini = normalize_iso_date(raw_ini)
        dt_fim = normalize_iso_date(raw_fim)

        if not dt_ini or not dt_fim or len(dt_ini) != 10 or len(dt_fim) != 10:
            messagebox.showwarning(
                "Informe o Período Completo",
                "Informe uma Data Inicial e uma Data Final válidas no formato DD/MM/AAAA\n"
                "(por exemplo: Início 01/10/2026 e Fim 31/10/2026) antes de aplicar o filtro.",
            )
            return

        if dt_ini > dt_fim:
            dt_ini, dt_fim = dt_fim, dt_ini

        # Padroniza o texto visível nas caixas para DD/MM/AAAA
        self.var_dt_inicio.set(_fmt_date_br(dt_ini))
        self.var_dt_fim.set(_fmt_date_br(dt_fim))

        self.period_filter_applied = True
        self.applied_dt_inicio_iso = dt_ini
        self.applied_dt_fim_iso = dt_fim

        # Garante que os valores da base Firebird (.FDB) estejam carregados e sincronizados ao aplicar a data
        fdb_path = self.var_db_path.get().strip()
        if fdb_path and os.path.exists(fdb_path):
            ods = inspect_fdb_ods_header(fdb_path)
            if ods and ods.get("validHeader") and not ods.get("isDemoFile"):
                ov = self.db.get_database_overview()
                nfv_count = int((ov.get("clippTables") or {}).get("tb_nfvenda") or 0)
                active_db = str((ov.get("activeConnection") or {}).get("database") or "")
                with self.db._get_sqlite_conn() as conn:
                    cur = conn.cursor()
                    cur.execute("SELECT COALESCE(SUM(TOT_NF), 0) FROM TB_NFVENDA_TOT")
                    tot_sum = float(cur.fetchone()[0] or 0.0)
                if nfv_count <= 15 or tot_sum <= 0.0 or os.path.normcase(active_db) != os.path.normcase(fdb_path):
                    self.lbl_sidebar_db_status.config(
                        text="Carregando valores da base Firebird (.FDB) para o período...",
                        fg="#38bdf8",
                    )
                    self.root.update_idletasks()
                    res = self.db.sync_clipp_fdb_to_local(fdb_path)
                    if res.get("ok"):
                        self.lbl_sidebar_db_status.config(
                            text=f"✓ Base sincronizada ({res['totalSalesSynced']} registros). Período ativo aplicado.",
                            fg="#34d399",
                        )

        self._update_step2_and_step3_lock_ui()
        self.refresh_all_views()

    def _on_date_base_changed(self) -> None:
        if self.period_filter_applied:
            self.refresh_all_views()

    def clear_period_filter(self) -> None:
        """Zera o filtro de período, volta todos os valores para R$ 0,00 e bloqueia o Passo 3."""
        self.var_dt_inicio.set("")
        self.var_dt_fim.set("")
        self.period_filter_applied = False
        self.applied_dt_inicio_iso = None
        self.applied_dt_fim_iso = None
        self._update_step2_and_step3_lock_ui()
        self.refresh_all_views()

    def browse_sidebar_fdb(self) -> None:
        path = filedialog.askopenfilename(
            initialdir=self.last_browse_dir,
            title="Selecionar Base Firebird (.FDB / .GDB)",
            filetypes=[("Base Firebird (.fdb, .gdb)", "*.fdb *.FDB *.gdb *.GDB"), ("Todos os arquivos", "*.*")],
        )
        if path:
            self.last_browse_dir = str(Path(path).parent)
            self.var_db_path.set(path)
            self.connect_and_sync_clipp_database()

    def connect_and_sync_clipp_database(self) -> None:
        fdb_path = self.var_db_path.get().strip()
        if not fdb_path or not os.path.exists(fdb_path):
            messagebox.showwarning("Base não encontrada", f"O arquivo informado não foi encontrado:\n{fdb_path}")
            return
        self.lbl_sidebar_db_status.config(text="Sincronizando registros de tb_nfvenda e tb_nfe...", fg="#38bdf8")
        self.root.update_idletasks()
        res = self.db.sync_clipp_fdb_to_local(fdb_path)
        if not res.get("ok"):
            ods = inspect_fdb_ods_header(fdb_path)
            if ods and ods.get("validHeader"):
                self.lbl_sidebar_db_status.config(
                    text=f"✓ Base conectada ({ods['detectedVersion']}). Aplique o período no Passo 2.",
                    fg="#34d399",
                )
                self.refresh_all_views()
                return
            messagebox.showerror("Erro ao Ler Base", res.get("error", "Falha desconhecida"))
            return

        self.lbl_sidebar_db_status.config(
            text=(
                f"✓ Base sincronizada ({res['totalSalesSynced']} registros). "
                + ("Período ativo aplicado." if self.period_filter_applied else "Aplique o período no Passo 2.")
            ),
            fg="#34d399",
        )
        self.refresh_all_views()
        messagebox.showinfo(
            "Base Firebird Sincronizada",
            f"Conexão e leitura da base concluídas com sucesso!\n\n"
            f"• Arquivo: {fdb_path}\n"
            f"• Documentos na base: {res['totalSalesSynced']}\n\n"
            + (
                "Os valores do período filtrado já foram atualizados."
                if self.period_filter_applied
                else "Os valores na tela permanecem zerados até que você aplique o Filtro de Período no Passo 2."
            ),
        )

    def _finalize_xml_import(self, new_entries: List[Dict[str, Any]], new_errors: List[Dict[str, str]], source_label: str) -> None:
        if not self.period_filter_applied:
            self._warn_period_required()
            return

        if not new_entries and new_errors:
            err_msg = "\n".join(f"• {e.get('fileName', '')}: {e.get('error') or e.get('message', '')}" for e in new_errors[:8])
            messagebox.showerror(
                "Não foi possível extrair XMLs",
                f"Nenhum arquivo XML válido foi encontrado em:\n{source_label}\n\nDetalhes:\n{err_msg}",
            )
            return

        if not new_entries:
            messagebox.showwarning(
                "Nenhum XML encontrado",
                f"O pacote ou pasta selecionada não contém arquivos .xml fiscais:\n{source_label}",
            )
            return

        if self.xml_entries:
            append = messagebox.askyesno(
                "Lote XML já carregado",
                f"Já existem {len(self.xml_documents)} documentos XML carregados.\n\n"
                f"Deseja SOMAR este novo pacote ({source_label}) aos XMLs atuais?\n"
                f"• Clique em SIM para somar (ex.: juntar lote NF-e 55 com lote NFC-e 65)\n"
                f"• Clique em NÃO para substituir pelo novo lote",
            )
            if append:
                self.xml_entries.extend(new_entries)
                self.loaded_archive_names.append(source_label)
            else:
                self.xml_entries = list(new_entries)
                self.loaded_archive_names = [source_label]
        else:
            self.xml_entries = list(new_entries)
            self.loaded_archive_names = [source_label]

        ana = analyze_extracted_xmls(self.xml_entries)
        self.xml_documents = ana["documents"]
        self.xml_alerts = new_errors + ana["validationAlerts"]
        self.xml_summary = ana["summary"]
        s = self.xml_summary

        self.lbl_sidebar_xml_status.config(
            text=(
                f"✓ {s['totalDocumentsParsed']} XMLs lidos | "
                f"55: {s['nfe55Count']} ({_fmt_brl(s['nfe55TotalValue'])}) | "
                f"65: {s['nfce65Count']} ({_fmt_brl(s['nfce65TotalValue'])})"
            ),
            fg="#34d399",
        )

        self.refresh_all_views()

        min_d = s.get("minIssueDate") or s.get("minAuthDate")
        max_d = s.get("maxIssueDate") or s.get("maxAuthDate")
        br_ini = _fmt_date_br(self.applied_dt_inicio_iso)
        br_fim = _fmt_date_br(self.applied_dt_fim_iso)

        messagebox.showinfo(
            "Lote XML (.ZIP / .RAR / .XML) Importado",
            f"Importação e confronto concluídos no período {br_ini} a {br_fim}!\n\n"
            f"• Origem: {', '.join(self.loaded_archive_names)}\n"
            f"• Total de XMLs extraídos do pacote: {s['totalFilesExtracted']}\n"
            f"• Datas encontradas nos XMLs: {_fmt_date_br(min_d)} a {_fmt_date_br(max_d)}\n\n"
            f"• NF-e (Modelo 55 Autorizadas no pacote): {s['nfe55Count']} notas — {_fmt_brl(s['nfe55TotalValue'])}\n"
            f"• NFC-e (Modelo 65 Autorizadas no pacote): {s['nfce65Count']} notas — {_fmt_brl(s['nfce65TotalValue'])}\n"
            f"• Canceladas (Status 101): {s['canceledCount']} | Inutilizadas (102): {s['inutilizedCount']}",
        )

    def browse_and_import_xmls(self) -> None:
        if not self.period_filter_applied:
            self._warn_period_required()
            return
        paths = filedialog.askopenfilenames(
            initialdir=self.last_browse_dir,
            title="Selecionar Arquivos .ZIP, .RAR ou .XML (NF-e 55 / NFC-e 65)",
            filetypes=[
                ("Lotes Fiscais (.zip, .rar, .xml)", "*.zip *.ZIP *.rar *.RAR *.xml *.XML"),
                ("Arquivos Compactados (.zip, .rar)", "*.zip *.ZIP *.rar *.RAR"),
                ("Arquivos XML (*.xml)", "*.xml *.XML"),
                ("Todos os arquivos (*.*)", "*.*"),
            ],
        )
        if not paths:
            return
        self.last_browse_dir = str(Path(paths[0]).parent)
        files = [{"name": os.path.basename(p), "buffer": Path(p).read_bytes()} for p in paths]
        ext = extract_xml_containers(files)
        label = ", ".join(os.path.basename(p) for p in paths[:3])
        if len(paths) > 3:
            label += f" (+{len(paths) - 3})"
        self._finalize_xml_import(ext["xmlEntries"], ext["extractionErrors"], label)

    def browse_and_import_xml_folder(self) -> None:
        if not self.period_filter_applied:
            self._warn_period_required()
            return
        folder = filedialog.askdirectory(
            initialdir=self.last_browse_dir,
            title="Selecionar Pasta Contendo Arquivos .XML, .ZIP ou .RAR",
        )
        if not folder:
            return
        self.last_browse_dir = folder
        ext = extract_xml_from_folder(folder)
        self._finalize_xml_import(ext["xmlEntries"], ext["extractionErrors"], os.path.basename(folder) or folder)

    def clear_imported_xmls(self) -> None:
        self.xml_entries = []
        self.xml_documents = []
        self.xml_alerts = []
        self.xml_summary = {}
        self.loaded_archive_names = []
        self.lbl_sidebar_xml_status.config(
            text="Nenhum lote XML (.zip/.rar) carregado.",
            fg=PALETTE["sb_muted"],
        )
        self.refresh_all_views()

    def browse_and_import_pdf(self) -> None:
        if not self.period_filter_applied:
            self._warn_period_required()
            return
        p = filedialog.askopenfilename(
            initialdir=self.last_browse_dir,
            title="Selecionar Relatório Contábil / Fiscal em PDF",
            filetypes=[("Arquivos PDF (*.pdf)", "*.pdf *.PDF"), ("Todos os arquivos (*.*)", "*.*")],
        )
        if not p:
            return
        self.last_browse_dir = str(Path(p).parent)
        self.pdf_last_bytes = Path(p).read_bytes()
        self.pdf_last_name = os.path.basename(p)
        res = parse_sales_pdf_bytes(self.pdf_last_bytes, self.pdf_last_name)
        if res.get("isScannedImagePdf"):
            if messagebox.askyesno(
                "PDF Digitalizado como Imagem",
                f"{res.get('scannedWarning')}\n\nDeseja executar o OCR Local agora?",
            ):
                res = perform_local_ocr_pdf_bytes(self.pdf_last_bytes, self.pdf_last_name)
        self.pdf_records = res.get("records") or []
        tot_pdf = sum(
            float(r.get("totalValue") or 0.0)
            for r in self.pdf_records
            if str(r.get("status") or "AUTORIZADO").upper() == "AUTORIZADO"
        )
        self.lbl_sidebar_pdf_status.config(
            text=f"✓ {self.pdf_last_name}: {len(self.pdf_records)} registros ({_fmt_brl(tot_pdf)})",
            fg="#34d399",
        )
        self.refresh_all_views()

    def clear_imported_pdf(self) -> None:
        self.pdf_records = []
        self.pdf_last_bytes = b""
        self.pdf_last_name = ""
        self.lbl_sidebar_pdf_status.config(
            text="Nenhum relatório PDF carregado (opcional).",
            fg=PALETTE["sb_muted"],
        )
        self.refresh_all_views()

    # =========================================================================
    # ATUALIZAÇÃO DAS VISÕES (ZERADO INICIAL vs PERÍODO APLICADO)
    # =========================================================================

    def _reset_views_to_zero(self) -> None:
        """Mantém absolutamente todos os KPIs e tabelas zerados enquanto o filtro de período não for aplicado."""
        self.reconciliation_result = None
        self.audit_dashboard = None
        self.last_db_audit_data = {}

        self.lbl_top_workflow_badge.configure(
            text="PASSO 2 PENDENTE — FILTRO OBRIGATÓRIO",
            bg="#fef3c7",
            fg="#92400e",
        )
        self.lbl_overview_scope.configure(
            text="Valores zerados (R$ 0,00). Informe a Data Início e Fim no Passo 2 e clique em '✓ APLICAR FILTRO DE PERÍODO' para carregar os dados e habilitar os XMLs."
        )

        # Zera KPIs
        self.kpi_bco_emi_val.config(text="R$ 0,00")
        self.kpi_bco_emi_sub.config(text="0 notas (Aguardando filtro de período)")

        self.kpi_bco_aut_val.config(text="R$ 0,00")
        self.kpi_bco_aut_sub.config(text="0 notas | Dif. D+1: 0 (R$ 0,00)")

        self.kpi_xml_val.config(text="R$ 0,00")
        self.kpi_xml_sub.config(text="0 XMLs (Bloqueado até filtrar período)")

        self.kpi_pdf_val.config(text="R$ 0,00")
        self.kpi_pdf_sub.config(text="0 registros no relatório PDF")

        self.kpi_bat_val.config(text="Aguardando Período", fg=PALETTE["text_muted"])
        self.kpi_bat_sub.config(text="Aplique o filtro no Passo 2 para iniciar")

        # Zera Comparativo Triplo com linhas 0,00
        for i in self.tree_triple.get_children():
            self.tree_triple.delete(i)
        for lbl in ("Consolidado Geral (55 + 65)", "NF-e Modelo 55", "NFC-e Modelo 65"):
            self.tree_triple.insert(
                "",
                "end",
                values=(
                    lbl, 0, "R$ 0,00", 0, "R$ 0,00", "R$ 0,00",
                    0, "R$ 0,00", 0, "R$ 0,00", "R$ 0,00", "Aguardando Período",
                ),
                tags=("zero",),
            )

        # Limpa todas as demais árvores
        for tree in (
            self.tree_d1,
            self.tree_cfop_55, self.tree_ncm_55, self.tree_docs_55,
            self.tree_cfop_65, self.tree_ncm_65, self.tree_docs_65,
            self.tree_events,
            self.tree_matrix,
        ):
            for i in tree.get_children():
                tree.delete(i)

        self.lbl_d1_header.config(
            text="Desafio Temporal (D+1 / Contingência): 0 notas (Aplique o filtro de período no Passo 2)"
        )
        self.lbl_sum_55.config(
            text="Modelo 55   |   Banco (Emissão): 0 notas (R$ 0,00)   |   Banco (Autorização): 0 notas (R$ 0,00)   |   XML: 0 notas (R$ 0,00)   |   Aguardando Período"
        )
        self.lbl_sum_65.config(
            text="Modelo 65   |   Banco (Emissão): 0 notas (R$ 0,00)   |   Banco (Autorização): 0 notas (R$ 0,00)   |   XML: 0 notas (R$ 0,00)   |   Aguardando Período"
        )
        self.lbl_events_summary.config(
            text="Eventos Isolados do Faturamento (Status 100) — Canceladas (101): 0 (R$ 0,00)  |  Inutilizadas (102): 0"
        )
        self.lbl_matrix_summary.config(
            text="Matriz de Divergências (0 ocorrências) — Aplique o filtro de período no Passo 2"
        )

    def refresh_all_views(self) -> None:
        if not self.period_filter_applied or not self.applied_dt_inicio_iso or not self.applied_dt_fim_iso:
            self._reset_views_to_zero()
            return

        dt_ini = self.applied_dt_inicio_iso
        dt_fim = self.applied_dt_fim_iso
        date_field = self.var_date_base.get() or "issueDate"

        db_audit_data = self.db.query_clipp_audit_dataset(
            dt_inicio=dt_ini,
            dt_fim=dt_fim,
            date_field=date_field,
        )
        self.last_db_audit_data = db_audit_data
        fb_records = self.db.query_mapped_sales(limit=50000, dt_inicio=dt_ini, dt_fim=dt_fim)["records"]

        opts = {
            "dateField": date_field,
            "startDate": dt_ini,
            "endDate": dt_fim,
            "comparePayment": True,
            "firebirdEnabled": True,
        }

        self.reconciliation_result = run_reconciliation(
            xml_documents=self.xml_documents,
            pdf_records=self.pdf_records,
            firebird_records=fb_records,
            options=opts,
            manual_decisions=self.manual_decisions,
            db_audit_data=db_audit_data,
        )
        self.audit_dashboard = self.reconciliation_result["auditDashboard"]

        per_txt = f"{_fmt_date_br(dt_ini)} a {_fmt_date_br(dt_fim)}"
        base_txt = "Autorização SEFAZ (tb_nfe.data_hora)" if date_field == "authDate" else "Data de Emissão (tb_nfvenda.dt_emissao)"
        xml_txt = (
            f"{len(self.xml_documents)} XMLs ({', '.join(self.loaded_archive_names)})"
            if self.xml_documents
            else "Aguardando importação no Passo 3"
        )
        self.lbl_top_workflow_badge.configure(
            text=f"✓ PERÍODO ATIVO: {per_txt}",
            bg="#dcfce7",
            fg="#166534",
        )
        self.lbl_overview_scope.config(
            text=f"Base Temporal: {base_txt}   |   Lote XML: {xml_txt}"
        )

        self._update_kpi_cards()
        self._populate_overview_trees()
        self._populate_model_tab("55", db_audit_data)
        self._populate_model_tab("65", db_audit_data)
        self._populate_events_tree()
        self._populate_divergence_matrix_tree()

    def _update_kpi_cards(self) -> None:
        if not self.audit_dashboard:
            return
        k = self.audit_dashboard["kpiCards"]
        bat = k["statusBatimento"]

        self.kpi_bco_emi_val.config(text=_fmt_brl(k["totalBancoEmissao"]))
        self.kpi_bco_emi_sub.config(text=f"{k['qtdBancoEmissao']} notas no período (dt_emissao)")

        self.kpi_bco_aut_val.config(text=_fmt_brl(k["totalBancoAutorizacao"]))
        self.kpi_bco_aut_sub.config(
            text=f"{k['qtdBancoAutorizacao']} notas | Dif. D+1: {k['qtdDPlus1']} ({_fmt_brl(k['divergenciaEmissaoVsAutorizacao'])})"
        )

        self.kpi_xml_val.config(text=_fmt_brl(k["totalXmlImportados"]))
        self.kpi_xml_sub.config(text=f"{k['qtdXmlImportados']} XMLs autorizados no período")

        self.kpi_pdf_val.config(text=_fmt_brl(k["totalRelatorioPdf"]))
        self.kpi_pdf_sub.config(text=f"{k['qtdRelatorioPdf']} registros no relatório PDF")

        self.kpi_bat_val.config(text=bat["label"])
        self.kpi_bat_sub.config(text=bat["detail"][:58])
        color = PALETTE["emerald"] if bat["badge"] == "success" else (PALETTE["rose"] if bat["badge"] == "danger" else PALETTE["text_secondary"])
        self.kpi_bat_val.config(fg=color)

    def _populate_overview_trees(self) -> None:
        if not self.audit_dashboard:
            return
        for i in self.tree_triple.get_children():
            self.tree_triple.delete(i)
        tc = self.audit_dashboard["tripleComparison"]
        for idx, key in enumerate(("ALL", "55", "65")):
            r = tc.get(key)
            if not r:
                continue
            dif_bx = r.get("diffBancoVsXml")
            dif_bx_str = _fmt_brl(dif_bx) if dif_bx is not None else "R$ 0,00"
            badge = (r.get("batimento") or {}).get("badge", "")
            tag = "bold" if key == "ALL" else ("ok" if badge == "success" else ("err" if badge == "danger" else ("even" if idx % 2 == 0 else "odd")))
            self.tree_triple.insert(
                "",
                "end",
                values=(
                    r["label"],
                    r["qtdBancoEmissao"],
                    _fmt_brl(r["totalBancoEmissao"]),
                    r["qtdBancoAutorizacao"],
                    _fmt_brl(r["totalBancoAutorizacao"]),
                    _fmt_brl(r["divergenciaDPlus1"]),
                    r["qtdXml"],
                    _fmt_brl(r["totalXml"]),
                    r["qtdPdf"],
                    _fmt_brl(r["totalPdf"]),
                    dif_bx_str,
                    r["batimento"]["label"],
                ),
                tags=(tag,),
            )

        for i in self.tree_d1.get_children():
            self.tree_d1.delete(i)
        d1_list = self.audit_dashboard.get("dPlus1Records") or []
        self.lbl_d1_header.config(
            text=f"Desafio Temporal (D+1 / Contingência): {len(d1_list)} notas no período com Data de Autorização SEFAZ diferente da Data de Emissão"
        )
        for idx, d in enumerate(d1_list[:500]):
            self.tree_d1.insert(
                "",
                "end",
                values=(
                    d.get("nf_numero", "—"),
                    d.get("nf_modelo", "—"),
                    d.get("nf_serie", "1"),
                    d.get("id_nfe", "—"),
                    _fmt_date_br(d.get("dt_emissao")),
                    _fmt_datetime_br(d.get("data_hora_sefaz")),
                    d.get("status_des", "—"),
                    _fmt_brl(d.get("valor")),
                ),
                tags=("warn" if idx % 2 == 0 else "odd",),
            )

    def _populate_model_tab(self, model_code: str, db_audit_data: Dict[str, Any]) -> None:
        if not self.audit_dashboard or not self.period_filter_applied:
            return
        tc_m = self.audit_dashboard["tripleComparison"].get(model_code, {})
        fg_m = self.audit_dashboard["fiscalGroupings"].get(model_code, {"cfop": [], "ncm": []})

        lbl_w = self.lbl_sum_55 if model_code == "55" else self.lbl_sum_65
        tree_cfop = self.tree_cfop_55 if model_code == "55" else self.tree_cfop_65
        tree_ncm = self.tree_ncm_55 if model_code == "55" else self.tree_ncm_65
        tree_docs = self.tree_docs_55 if model_code == "55" else self.tree_docs_65
        search_q = (self.var_search_55.get() if model_code == "55" else self.var_search_65.get()).strip().lower()

        lbl_w.config(
            text=(
                f"Modelo {model_code}   |   "
                f"Banco (Emissão): {tc_m.get('qtdBancoEmissao', 0)} notas ({_fmt_brl(tc_m.get('totalBancoEmissao', 0.0))})   |   "
                f"Banco (Autorização): {tc_m.get('qtdBancoAutorizacao', 0)} notas ({_fmt_brl(tc_m.get('totalBancoAutorizacao', 0.0))})   |   "
                f"XML Importado: {tc_m.get('qtdXml', 0)} notas ({_fmt_brl(tc_m.get('totalXml', 0.0))})   |   "
                f"Resultado: {(tc_m.get('batimento') or {}).get('label', '')}"
            )
        )

        # Popula Tabela Dinâmica CFOP
        for i in tree_cfop.get_children():
            tree_cfop.delete(i)
        for idx, c in enumerate(fg_m.get("cfop") or []):
            cfop_code = str(c["cfop"])
            desc_cfop = CFOP_DESCRIPTIONS.get(cfop_code, "Operação Fiscal de Saída / Venda")
            tag = "ok" if c["status"] == "Bateu" else ("err" if self.xml_documents else ("even" if idx % 2 == 0 else "odd"))
            tree_cfop.insert(
                "",
                "end",
                values=(
                    cfop_code,
                    desc_cfop,
                    c["qtdNotasBanco"],
                    _fmt_brl(c["baseCalculoBanco"]),
                    _fmt_brl(c["valorIcmsBanco"]),
                    _fmt_brl(c["valorContabilBanco"]),
                    c["qtdNotasXml"],
                    _fmt_brl(c["baseCalculoXml"]),
                    _fmt_brl(c["valorIcmsXml"]),
                    _fmt_brl(c["valorContabilXml"]),
                    _fmt_brl(c["difValorContabil"]),
                    c["status"] if self.xml_documents else "Aguardando XML",
                ),
                tags=(tag,),
            )

        # Popula Tabela Dinâmica NCM
        for i in tree_ncm.get_children():
            tree_ncm.delete(i)
        for idx, n in enumerate((fg_m.get("ncm") or [])[:400]):
            tag = "ok" if n["status"] == "Bateu" else ("err" if self.xml_documents else ("even" if idx % 2 == 0 else "odd"))
            tree_ncm.insert(
                "",
                "end",
                values=(
                    n["ncm"],
                    n["descricao"],
                    n["qtdItensBanco"],
                    _fmt_brl(n["baseCalculoBanco"]),
                    _fmt_brl(n["valorContabilBanco"]),
                    n["qtdItensXml"],
                    _fmt_brl(n["baseCalculoXml"]),
                    _fmt_brl(n["valorContabilXml"]),
                    _fmt_brl(n["difValorContabil"]),
                    n["status"] if self.xml_documents else "Aguardando XML",
                ),
                tags=(tag,),
            )

        xml_keys_mod = set()
        xml_nums_mod = set()
        for x in self.xml_documents:
            if str(x.get("model")) == model_code and not x.get("isDuplicate"):
                if x.get("accessKey"):
                    xml_keys_mod.add(str(x["accessKey"]))
                if x.get("number"):
                    xml_nums_mod.add(str(int(re.sub(r"\D", "", str(x["number"])) or 0)))

        # Popula Relação de Notas do Modelo
        for i in tree_docs.get_children():
            tree_docs.delete(i)
        docs_raw = [
            r for r in (db_audit_data.get("records") or [])
            if str(r.get("nf_modelo")) == model_code
            and str(r.get("status_codigo")) == "100"
            and r.get("in_active_period", True)
        ]
        for idx, d in enumerate(docs_raw):
            num_str = str(d.get("nf_numero") or "")
            key_str = str(d.get("id_nfe") or "")
            if search_q and search_q not in num_str.lower() and search_q not in key_str.lower():
                continue
            if not self.xml_documents:
                st_conc = "Aguardando XML (.zip/.rar)"
                tag = "even" if idx % 2 == 0 else "odd"
            elif (key_str and key_str in xml_keys_mod) or (num_str and num_str in xml_nums_mod):
                st_conc = "✓ Conciliado com XML"
                tag = "ok"
            else:
                st_conc = "⚠ Faltando no XML"
                tag = "err"

            tree_docs.insert(
                "",
                "end",
                values=(
                    num_str or "—",
                    d.get("nf_serie", "1"),
                    key_str or "—",
                    _fmt_date_br(d.get("dt_emissao")),
                    _fmt_datetime_br(d.get("data_hora_sefaz")),
                    d.get("cfop", "5102"),
                    _fmt_brl(d.get("base_icms")),
                    _fmt_brl(d.get("valor_icms")),
                    _fmt_brl(d.get("valor_total") or d.get("valor")),
                    st_conc,
                ),
                tags=(tag,),
            )

    def _populate_events_tree(self) -> None:
        for i in self.tree_events.get_children():
            self.tree_events.delete(i)
        if not self.audit_dashboard or not self.period_filter_applied:
            return
        ev_data = self.audit_dashboard.get("separateEvents") or {}
        f_val = self.var_events_filter.get()
        if f_val == "101":
            rows = ev_data.get("canceladas101") or []
        elif f_val == "102":
            rows = ev_data.get("inutilizadas102") or []
        else:
            rows = ev_data.get("allEvents") or []

        qtd_101 = ev_data.get("qtdCanceladas101", 0)
        val_101 = ev_data.get("valorCanceladas101", 0.0)
        qtd_102 = ev_data.get("qtdInutilizadas102", 0)

        self.lbl_events_summary.config(
            text=(
                f"Eventos Isolados do Faturamento (Status 100) — "
                f"Canceladas (101): {qtd_101} ({_fmt_brl(val_101)})  |  "
                f"Inutilizadas (102): {qtd_102}"
            )
        )
        for idx, r in enumerate(rows[:500]):
            fontes = []
            if r.get("constaBanco"):
                fontes.append("Banco")
            if r.get("constaXml"):
                fontes.append("XML")
            if r.get("constaPdf"):
                fontes.append("PDF")
            self.tree_events.insert(
                "",
                "end",
                values=(
                    r.get("status_codigo", ""),
                    r.get("status", ""),
                    r.get("nf_numero", "—"),
                    r.get("nf_serie", "1"),
                    r.get("nf_modelo", ""),
                    r.get("id_nfe", "—"),
                    _fmt_date_br(r.get("dt_emissao")),
                    _fmt_datetime_br(r.get("data_hora_sefaz")),
                    r.get("status_des", ""),
                    _fmt_brl(r.get("valor")),
                    " + ".join(fontes) or "—",
                ),
                tags=("warn" if idx % 2 == 0 else "odd",),
            )

    def _populate_divergence_matrix_tree(self) -> None:
        for i in self.tree_matrix.get_children():
            self.tree_matrix.delete(i)
        if not self.audit_dashboard or not self.period_filter_applied:
            return

        raw_dm = self.audit_dashboard.get("divergenceMatrix")
        if isinstance(raw_dm, dict):
            rows = raw_dm.get("rows") or []
            counts = raw_dm.get("counts") or {}
        else:
            rows = raw_dm or []
            counts = self.audit_dashboard.get("divergenceCounts") or {}

        f_code = self.var_matrix_filter.get()
        q_search = self.var_matrix_search.get().strip().lower()

        self.lbl_matrix_summary.config(
            text=(
                f"Matriz de Divergências ({counts.get('total', len(rows))} ocorrências no período) — "
                f"Banco sem XML: {counts.get('BANCO_SEM_XML', 0)} | "
                f"XML sem Banco/PDF: {counts.get('XML_SEM_RELATORIO_OU_BANCO', 0)} | "
                f"PDF sem XML: {counts.get('RELATORIO_SEM_XML', 0)} | "
                f"D+1: {counts.get('DIVERGENCIA_D_MAIS_1', 0)}"
            )
        )

        for idx, r in enumerate(rows):
            c_falha = r.get("codigo_falha", "")
            if f_code == "RELATORIO_SEM_XML" and c_falha != "RELATORIO_SEM_XML":
                continue
            if f_code == "XML_SEM_RELATORIO_OU_BANCO" and c_falha not in ("XML_SEM_RELATORIO_E_BANCO", "XML_SEM_BANCO", "XML_SEM_RELATORIO"):
                continue
            if f_code == "BANCO_SEM_XML" and c_falha != "BANCO_SEM_XML":
                continue
            if f_code == "DIVERGENCIA_D_MAIS_1" and c_falha != "DIVERGENCIA_D_MAIS_1":
                continue
            if f_code == "OUTRAS" and c_falha not in ("DIVERGENCIA_VALOR", "CHAVE_INVALIDA_OU_AUSENTE", "DIVERGENCIA_CHAVE_SERIE"):
                continue
            if q_search:
                hay = f"{r.get('nf_numero', '')} {r.get('id_nfe', '')} {r.get('origem_falha', '')}".lower()
                if q_search not in hay:
                    continue

            tag = "warn" if c_falha == "DIVERGENCIA_D_MAIS_1" else ("err" if idx % 2 == 0 else "odd")
            self.tree_matrix.insert(
                "",
                "end",
                values=(
                    r.get("nf_numero", "—"),
                    r.get("nf_modelo", "—"),
                    r.get("id_nfe", "—"),
                    _fmt_date_br(r.get("dt_emissao")),
                    _fmt_datetime_br(r.get("data_hora_sefaz")),
                    r.get("status_des", "—"),
                    _fmt_brl(r.get("valor")),
                    r.get("origem_falha", "—"),
                ),
                tags=(tag,),
            )

    # -------------------------------------------------------------------------
    # CONSOLE SQL E EXPORTAÇÃO (.XLSX / .PDF)
    # -------------------------------------------------------------------------
    def set_and_run_sql(self, sql: str) -> None:
        self.txt_sql.delete("1.0", "end")
        self.txt_sql.insert("1.0", sql)
        self.run_sql_console_query()

    def run_sql_console_query(self) -> None:
        sql = self.txt_sql.get("1.0", "end").strip()
        try:
            res = self.db.execute_read_only_sql(sql)
            cols = res["columns"]
            rows = res["rows"]
            for i in self.tree_sql.get_children():
                self.tree_sql.delete(i)
            self.tree_sql["columns"] = cols
            for c in cols:
                self.tree_sql.heading(c, text=c)
                self.tree_sql.column(c, width=max(105, min(260, len(c) * 12)), anchor="w")
            for idx, r in enumerate(rows[:1000]):
                self.tree_sql.insert(
                    "", "end", values=[r.get(c, "") for c in cols], tags=("even" if idx % 2 == 0 else "odd",)
                )
        except Exception as exc:
            messagebox.showerror("Erro SQL (Somente-Leitura)", str(exc))

    def _build_export_metadata(self) -> Dict[str, Any]:
        return {
            "executedAt": (self.reconciliation_result or {}).get("executedAt", ""),
            "xmlFiles": self.loaded_archive_names or (["Lote XML Importado"] if self.xml_documents else ["Nenhum"]),
            "pdfFile": self.pdf_last_name or "Nenhum",
            "firebirdDb": self.var_db_path.get(),
            "dateField": "Autorização SEFAZ (data_hora)" if self.var_date_base.get() == "authDate" else "Data de Emissão (dt_emissao)",
            "period": f"{self.var_dt_inicio.get() or 'Início'} a {self.var_dt_fim.get() or 'Fim'}",
        }

    def export_xlsx_dialog(self) -> None:
        if not self.period_filter_applied or not self.reconciliation_result:
            self._warn_period_required()
            return
        path = filedialog.asksaveasfilename(
            initialdir=self.last_browse_dir,
            title="Salvar Planilha de Auditoria Fiscal (.xlsx)",
            defaultextension=".xlsx",
            initialfile="auditoria_fiscal_conferefiscal.xlsx",
            filetypes=[("Planilha Excel (*.xlsx)", "*.xlsx")],
        )
        if not path:
            return
        data = generate_xlsx_export_bytes(
            self.reconciliation_result["items"],
            self.reconciliation_result["summary"],
            self.audit_dashboard,
        )
        Path(path).write_bytes(data)
        messagebox.showinfo("Exportação Excel Concluída", f"Planilha Excel (.xlsx) gerada com cabeçalho de auditoria em:\n{path}")

    def export_pdf_dialog(self) -> None:
        if not self.period_filter_applied or not self.reconciliation_result:
            self._warn_period_required()
            return
        path = filedialog.asksaveasfilename(
            initialdir=self.last_browse_dir,
            title="Salvar Relatório PDF de Auditoria Fiscal",
            defaultextension=".pdf",
            initialfile="relatorio_auditoria_fiscal.pdf",
            filetypes=[("Relatório PDF (*.pdf)", "*.pdf")],
        )
        if not path:
            return
        data = generate_pdf_export_bytes(
            self.reconciliation_result["items"],
            self.reconciliation_result["summary"],
            self._build_export_metadata(),
            self.audit_dashboard,
        )
        Path(path).write_bytes(data)
        messagebox.showinfo("Exportação PDF Concluída", f"Relatório PDF com cabeçalho de auditoria salvo em:\n{path}")

    def start_and_open_web_ui(self) -> None:
        port = 5000
        if self.web_server_thread is None or not self.web_server_thread.is_alive():
            def _serve():
                try:
                    httpd = ThreadingHTTPServer(("127.0.0.1", port), ConfereFiscalPythonHandler)
                    httpd.serve_forever()
                except OSError:
                    pass

            self.web_server_thread = threading.Thread(target=_serve, daemon=True)
            self.web_server_thread.start()
        webbrowser.open(f"http://127.0.0.1:{port}")

    # =========================================================================
    # CONTROLE DE VERSÃO E ATUALIZAÇÃO AUTOMÁTICA (GITHUB junin1737/conferefiscal)
    # =========================================================================

    def check_version_status(self) -> Dict[str, Any]:
        """Consulta a versão publicada no GitHub e atualiza os indicadores visuais da tela."""
        self.version_info = get_installed_version_info()
        self.installed_version = str(self.version_info.get("version") or "1.1.0")
        info = check_published_version()
        self.last_version_check = info

        pub_ver = info.get("publishedVersion")
        if info.get("updateAvailable") and pub_ver:
            self.lbl_top_version_info.config(
                text=f"Instalada: v{self.installed_version}  |  Publicada: v{pub_ver} (Nova!)",
                bg="#fef3c7",
                fg="#92400e",
            )
            self.btn_check_update.set_text(f"⬆ Atualizar para v{pub_ver}")
            self.btn_sidebar_update.set_text(f"⬆ Atualizar para v{pub_ver}")
        elif pub_ver:
            self.lbl_top_version_info.config(
                text=f"Instalada: v{self.installed_version}  |  Publicada: v{pub_ver} ✓",
                bg="#dcfce7",
                fg="#166534",
            )
            self.btn_check_update.set_text("🔄 Atualizar")
            self.btn_sidebar_update.set_text(f"🔄 Atualizar (Instalada v{self.installed_version})")
        else:
            self.lbl_top_version_info.config(
                text=f"Instalada: v{self.installed_version}  |  Publicada: —",
                bg="#f1f5f9",
                fg=PALETTE["text_secondary"],
            )
        return info

    def open_version_and_update_dialog(self) -> None:
        """
        Abre a Central de Versão e Atualização (checando a versão instalada vs. a versão publicada
        em https://github.com/junin1737/conferefiscal e permitindo atualizar com 1 clique).
        """
        win = tk.Toplevel(self.root)
        win.title("Atualização e Controle de Versão — ConfereFiscal")
        win.geometry("680x510")
        win.minsize(640, 470)
        win.configure(bg=PALETTE["app_bg"])
        win.transient(self.root)
        win.grab_set()
        if self.icon_photo is not None:
            try:
                win.iconphoto(False, self.icon_photo)
            except Exception:
                pass

        # Cabeçalho escuro executivo
        hdr = tk.Frame(win, bg=PALETTE["sb_bg"], padx=18, pady=14)
        hdr.pack(fill="x")
        tk.Label(
            hdr,
            text="Central de Versão e Atualização (GitHub)",
            bg=PALETTE["sb_bg"],
            fg="#ffffff",
            font=("Segoe UI", 13, "bold"),
        ).pack(anchor="w")
        tk.Label(
            hdr,
            text="Repositório Oficial: https://github.com/junin1737/conferefiscal",
            bg=PALETTE["sb_bg"],
            fg="#38bdf8",
            font=("Segoe UI", 9),
        ).pack(anchor="w", pady=(2, 0))

        body = tk.Frame(win, bg=PALETTE["app_bg"], padx=16, pady=12)
        body.pack(fill="both", expand=True)

        # Card Comparativo: Versão Instalada vs. Versão Publicada
        cmp_outer, cmp_inner = self._create_surface_card(body, accent_top=PALETTE["sky"], padx=14, pady=12)
        cmp_outer.pack(fill="x", pady=(0, 10))

        cards_row = tk.Frame(cmp_inner, bg=PALETTE["card_bg"])
        cards_row.pack(fill="x", pady=(0, 8))
        cards_row.columnconfigure(0, weight=1)
        cards_row.columnconfigure(1, weight=1)

        # Box 1: Versão Instalada
        b_inst = tk.Frame(cards_row, bg="#f8fafc", highlightthickness=1, highlightbackground="#cbd5e1", padx=12, pady=8)
        b_inst.grid(row=0, column=0, sticky="nsew", padx=(0, 6))
        tk.Label(b_inst, text="VERSÃO INSTALADA NESTA MÁQUINA", bg="#f8fafc", fg=PALETTE["text_secondary"], font=("Segoe UI", 8, "bold")).pack(anchor="w")
        lbl_inst_val = tk.Label(b_inst, text=f"v{self.installed_version}", bg="#f8fafc", fg=PALETTE["text_primary"], font=("Segoe UI", 16, "bold"))
        lbl_inst_val.pack(anchor="w", pady=(2, 1))
        lbl_inst_sub = tk.Label(b_inst, text=f"Compilação local: {self.version_info.get('releaseDate', '—')}", bg="#f8fafc", fg=PALETTE["text_muted"], font=("Segoe UI", 8))
        lbl_inst_sub.pack(anchor="w")

        # Box 2: Versão Publicada no GitHub
        b_pub = tk.Frame(cards_row, bg="#f8fafc", highlightthickness=1, highlightbackground="#cbd5e1", padx=12, pady=8)
        b_pub.grid(row=0, column=1, sticky="nsew", padx=(6, 0))
        tk.Label(b_pub, text="VERSÃO PUBLICADA (GITHUB)", bg="#f8fafc", fg=PALETTE["text_secondary"], font=("Segoe UI", 8, "bold")).pack(anchor="w")
        lbl_pub_val = tk.Label(b_pub, text="Verificando...", bg="#f8fafc", fg=PALETTE["blue"], font=("Segoe UI", 16, "bold"))
        lbl_pub_val.pack(anchor="w", pady=(2, 1))
        lbl_pub_sub = tk.Label(b_pub, text="Consultando junin1737/conferefiscal...", bg="#f8fafc", fg=PALETTE["text_muted"], font=("Segoe UI", 8))
        lbl_pub_sub.pack(anchor="w")

        lbl_status_banner = tk.Label(
            cmp_inner,
            text="Consultando versão publicada no GitHub...",
            bg="#eff6ff",
            fg="#1e3a8a",
            font=("Segoe UI", 9, "bold"),
            padx=10,
            pady=6,
            justify="left",
            anchor="w",
        )
        lbl_status_banner.pack(fill="x")

        # Card Changelog / Notas de Versão
        log_outer, log_inner = self._create_surface_card(body, padx=14, pady=10)
        log_outer.pack(fill="both", expand=True, pady=(0, 10))
        tk.Label(
            log_inner,
            text="Histórico de Alterações e Notas da Versão Publicada (Changelog):",
            bg=PALETTE["card_bg"],
            fg=PALETTE["text_primary"],
            font=("Segoe UI", 9, "bold"),
        ).pack(anchor="w", pady=(0, 6))

        txt_changelog = tk.Text(
            log_inner,
            height=8,
            bg="#f8fafc",
            fg=PALETTE["text_primary"],
            font=("Segoe UI", 9),
            relief="flat",
            padx=10,
            pady=8,
            wrap="word",
        )
        txt_changelog.pack(fill="both", expand=True)

        # Barra de Botões Inferior
        btn_bar = tk.Frame(body, bg=PALETTE["app_bg"])
        btn_bar.pack(fill="x")

        def _refresh_check() -> None:
            lbl_pub_val.config(text="Consultando...", fg=PALETTE["blue"])
            lbl_status_banner.config(text="Verificando versão publicada em github.com/junin1737/conferefiscal...", bg="#eff6ff", fg="#1e3a8a")
            win.update_idletasks()

            info = self.check_version_status()
            lbl_inst_val.config(text=f"v{info['installedVersion']}")
            lbl_inst_sub.config(text=f"Data da versão instalada: {info.get('installedReleaseDate') or '—'}")

            pub_v = info.get("publishedVersion")
            if pub_v:
                lbl_pub_val.config(
                    text=f"v{pub_v}",
                    fg=PALETTE["emerald"] if not info.get("updateAvailable") else PALETTE["amber"],
                )
                lbl_pub_sub.config(text=f"Publicado em: {info.get('publishedReleaseDate') or 'GitHub main'}")
            else:
                lbl_pub_val.config(text="Não localizada", fg=PALETTE["text_muted"])
                lbl_pub_sub.config(text="Repositório sem version.json publicado ou sem internet")

            if info.get("updateAvailable"):
                lbl_status_banner.config(
                    text=f"{info['statusTitle']} — Clique em 'Baixar e Atualizar Agora' para instalar a v{pub_v}.",
                    bg="#fef3c7",
                    fg="#92400e",
                )
            elif info.get("ok"):
                lbl_status_banner.config(
                    text=f"{info['statusTitle']} — A versão instalada (v{info['installedVersion']}) está igual ou superior à publicada (v{pub_v}).",
                    bg="#dcfce7",
                    fg="#166534",
                )
            else:
                lbl_status_banner.config(
                    text=info.get("statusMessage", "Não foi possível consultar o GitHub no momento."),
                    bg="#fff1f2",
                    fg="#9f1239",
                )

            txt_changelog.configure(state="normal")
            txt_changelog.delete("1.0", "end")
            for item in info.get("changelog") or []:
                txt_changelog.insert("end", f"• {item}\n")
            txt_changelog.configure(state="disabled")

        def _do_update() -> None:
            info = self.last_version_check or self.check_version_status()
            pub_v = info.get("publishedVersion")
            if pub_v and not info.get("updateAvailable"):
                if not messagebox.askyesno(
                    "Versão Já Atualizada",
                    f"A versão instalada (v{info['installedVersion']}) já é a versão mais recente (v{pub_v}).\n\n"
                    f"Deseja forçar a reinstalação/sincronização dos arquivos a partir do GitHub mesmo assim?",
                    parent=win,
                ):
                    return

            lbl_status_banner.config(
                text="Baixando e aplicando atualização do repositório GitHub (junin1737/conferefiscal)...",
                bg="#eff6ff",
                fg="#1e3a8a",
            )
            win.update_idletasks()

            upd_res = apply_published_update(
                remote_info=info,
                progress_callback=lambda msg: (lbl_status_banner.config(text=msg), win.update_idletasks()),
            )
            if upd_res.get("ok"):
                _refresh_check()
                messagebox.showinfo("Atualização Concluída", upd_res.get("message", "Aplicação atualizada com sucesso!"), parent=win)
            else:
                lbl_status_banner.config(text="Falha ao aplicar atualização automática.", bg="#fff1f2", fg="#9f1239")
                messagebox.showerror("Erro ao Atualizar", upd_res.get("message", "Erro desconhecido."), parent=win)

        ModernButton(
            btn_bar,
            text="🔄 Verificar Novamente",
            command=_refresh_check,
            bg=PALETTE["blue"],
            hover_bg=PALETTE["blue_hover"],
            padx=14,
            pady=6,
            font=("Segoe UI", 9, "bold"),
        ).pack(side="left", padx=(0, 8))

        ModernButton(
            btn_bar,
            text="⬇ Baixar e Atualizar Agora",
            command=_do_update,
            bg=PALETTE["emerald"],
            hover_bg=PALETTE["emerald_hover"],
            padx=14,
            pady=6,
            font=("Segoe UI", 9, "bold"),
        ).pack(side="left", padx=(0, 8))

        ModernButton(
            btn_bar,
            text="🌐 Abrir GitHub",
            command=lambda: webbrowser.open("https://github.com/junin1737/conferefiscal"),
            bg="#334155",
            hover_bg="#475569",
            padx=12,
            pady=6,
            font=("Segoe UI", 9, "bold"),
        ).pack(side="left")

        ModernButton(
            btn_bar,
            text="Fechar",
            command=win.destroy,
            bg="#e2e8f0",
            hover_bg="#cbd5e1",
            fg=PALETTE["text_primary"],
            padx=14,
            pady=6,
            font=("Segoe UI", 9, "bold"),
        ).pack(side="right")

        win.after(80, _refresh_check)


def launch_desktop_app() -> None:
    root = tk.Tk()
    try:
        app = ConfereFiscalDesktopApp(root)
    except Exception:
        err = traceback.format_exc()
        messagebox.showerror("Erro ao Iniciar ConfereFiscal", f"Ocorreu um erro ao montar a interface:\n\n{err}")
        root.destroy()
        return
    root.mainloop()


if __name__ == "__main__":
    launch_desktop_app()
