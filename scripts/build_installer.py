import io
import os
import sys
import zlib
import shutil
import struct
import zipfile
import subprocess
from pathlib import Path
from datetime import datetime

ROOT_DIR = Path(__file__).resolve().parent.parent
PYTHON_BASE_DIR = Path(r"C:\Python312AppPedidos")
CSC_PATH = Path(r"C:\Windows\Microsoft.NET\Framework64\v4.0.30319\csc.exe")
SYSTEM32_DIR = Path(r"C:\Windows\System32")
FB25_CLIENT_DIR = Path(r"C:\Work\MT\Cliente\Clipp\Exe\migracaoFirebird\Firebird_2_5")
FB50_CLIENT_DIR = Path(r"C:\Work\MT\Cliente\Clipp\Exe\migracaoFirebird\Firebird_5_0")

DIST_DIR = ROOT_DIR / "instalador"
SOURCE_ICON_PNG = DIST_DIR / "icone.png"
BUILD_STAGE_DIR = DIST_DIR / "_build_stage"
PORTABLE_STAGE_DIR = BUILD_STAGE_DIR / "ConfereFiscal"


def _decode_rgba_png(png_bytes: bytes):
    """
    Decodifica um PNG 8-bit RGBA não entrelaçado (como instalador/icone.png)
    em puro Python (usando zlib) para gerar entradas BMP 32x32 e 48x48 no .ico.
    """
    if png_bytes[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("Assinatura PNG inválida")
    pos = 8
    width = height = bit_depth = color_type = 0
    idat_chunks = []
    while pos < len(png_bytes):
        length = struct.unpack(">I", png_bytes[pos : pos + 4])[0]
        chunk_type = png_bytes[pos + 4 : pos + 8]
        chunk_data = png_bytes[pos + 8 : pos + 8 + length]
        pos += 12 + length
        if chunk_type == b"IHDR":
            width, height, bit_depth, color_type, _, _, _ = struct.unpack(">IIBBBBB", chunk_data)
        elif chunk_type == b"IDAT":
            idat_chunks.append(chunk_data)
        elif chunk_type == b"IEND":
            break

    if bit_depth != 8 or color_type not in (6, 2):
        raise ValueError(f"Formato PNG não suportado diretamente: bit_depth={bit_depth}, color_type={color_type}")

    bpp = 4 if color_type == 6 else 3
    stride = width * bpp
    raw = zlib.decompress(b"".join(idat_chunks))

    recon = bytearray(height * width * 4)
    prev_row = bytearray(stride)
    cur_row = bytearray(stride)
    in_pos = 0

    def _paeth(a: int, b: int, c: int) -> int:
        p = a + b - c
        pa = abs(p - a)
        pb = abs(p - b)
        pc = abs(p - c)
        if pa <= pb and pa <= pc:
            return a
        if pb <= pc:
            return b
        return c

    for y in range(height):
        f_type = raw[in_pos]
        in_pos += 1
        row_bytes = raw[in_pos : in_pos + stride]
        in_pos += stride

        for i in range(stride):
            x_byte = row_bytes[i]
            a = cur_row[i - bpp] if i >= bpp else 0
            b = prev_row[i]
            c = prev_row[i - bpp] if i >= bpp else 0
            if f_type == 0:
                val = x_byte
            elif f_type == 1:
                val = (x_byte + a) & 0xFF
            elif f_type == 2:
                val = (x_byte + b) & 0xFF
            elif f_type == 3:
                val = (x_byte + ((a + b) >> 1)) & 0xFF
            elif f_type == 4:
                val = (x_byte + _paeth(a, b, c)) & 0xFF
            else:
                val = x_byte
            cur_row[i] = val

        out_row_base = y * width * 4
        if bpp == 4:
            recon[out_row_base : out_row_base + width * 4] = cur_row
        else:
            for x in range(width):
                recon[out_row_base + x * 4 : out_row_base + x * 4 + 4] = bytes(
                    [cur_row[x * 3], cur_row[x * 3 + 1], cur_row[x * 3 + 2], 255]
                )
        prev_row[:] = cur_row

    return width, height, recon


def _build_bmp_ico_entry_from_rgba(src_w: int, src_h: int, rgba: bytearray, target_size: int) -> bytes:
    """
    Redimensiona com amostragem de área (box filter) de (src_w, src_h) para (target_size, target_size)
    e monta o bloco DIB (BITMAPINFOHEADER + BGRA bottom-up + AND mask) para arquivo .ICO.
    """
    pixels = bytearray(target_size * target_size * 4)
    scale_x = src_w / float(target_size)
    scale_y = src_h / float(target_size)

    for ty in range(target_size):
        # Coordenada invertida para BMP bottom-up
        src_ty = target_size - 1 - ty
        y0 = int(src_ty * scale_y)
        y1 = max(y0 + 1, min(src_h, int((src_ty + 1) * scale_y)))
        for tx in range(target_size):
            x0 = int(tx * scale_x)
            x1 = max(x0 + 1, min(src_w, int((tx + 1) * scale_x)))
            r_sum = g_sum = b_sum = a_sum = count = 0
            for sy in range(y0, y1):
                base_y = sy * src_w * 4
                for sx in range(x0, x1):
                    idx_s = base_y + sx * 4
                    r_sum += rgba[idx_s]
                    g_sum += rgba[idx_s + 1]
                    b_sum += rgba[idx_s + 2]
                    a_sum += rgba[idx_s + 3]
                    count += 1
            out_idx = (ty * target_size + tx) * 4
            if count > 0:
                pixels[out_idx] = b_sum // count
                pixels[out_idx + 1] = g_sum // count
                pixels[out_idx + 2] = r_sum // count
                pixels[out_idx + 3] = a_sum // count

    and_stride = ((target_size + 31) // 32) * 4
    and_mask = bytes(and_stride * target_size)
    bmi_header = struct.pack(
        "<IIIHHIIIIII",
        40,
        target_size,
        target_size * 2,
        1,
        32,
        0,
        len(pixels) + len(and_mask),
        0,
        0,
        0,
        0,
    )
    return bmi_header + bytes(pixels) + and_mask


def create_app_ico(ico_path: Path) -> None:
    """
    Converte o ícone oficial C:\\Work\\Pessoal\\Projetos\\ConfereFiscal\\instalador\\icone.png
    em um arquivo Windows .ICO multi-resolução (32x32 BMP + 48x48 BMP + 256x256 PNG)
    totalmente compatível com Windows 7, 8, 8.1, 10 e 11.
    """
    ico_path.parent.mkdir(parents=True, exist_ok=True)
    if SOURCE_ICON_PNG.exists():
        png_bytes = SOURCE_ICON_PNG.read_bytes()
        try:
            src_w, src_h, rgba = _decode_rgba_png(png_bytes)
            bmp32 = _build_bmp_ico_entry_from_rgba(src_w, src_h, rgba, 32)
            bmp48 = _build_bmp_ico_entry_from_rgba(src_w, src_h, rgba, 48)
            entries_data = [
                (32, 32, bmp32),
                (48, 48, bmp48),
                (0, 0, png_bytes),  # 0,0 = 256x256+ PNG nativo no padrão ICO Vista/Win7/8/10/11
            ]
            ico_header = struct.pack("<HHH", 0, 1, len(entries_data))
            offset = 6 + 16 * len(entries_data)
            dir_entries = []
            payloads = []
            for w_b, h_b, blob in entries_data:
                dir_entries.append(
                    struct.pack(
                        "<BBBBHHII",
                        w_b,
                        h_b,
                        0,
                        0,
                        1,
                        32,
                        len(blob),
                        offset,
                    )
                )
                payloads.append(blob)
                offset += len(blob)
            ico_path.write_bytes(ico_header + b"".join(dir_entries) + b"".join(payloads))
            return
        except Exception as exc:
            print(f"  [Aviso] Fallback na conversão PNG->ICO ({exc}); embutindo PNG direto.")
            ico_header = struct.pack("<HHH", 0, 1, 1)
            ico_dir = struct.pack("<BBBBHHII", 0, 0, 0, 0, 1, 32, len(png_bytes), 22)
            ico_path.write_bytes(ico_header + ico_dir + png_bytes)
            return

    raise FileNotFoundError(f"Ícone oficial não encontrado em: {SOURCE_ICON_PNG}")


def write_win7_to_win11_manifest(manifest_path: Path) -> None:
    """
    Gera o manifesto Win32 oficial declarando compatibilidade explícita com:
    Windows 7, Windows 8, Windows 8.1, Windows 10 e Windows 11 (sem exigir elevação UAC).
    """
    manifest_xml = """<?xml version="1.0" encoding="utf-8"?>
<assembly manifestVersion="1.0" xmlns="urn:schemas-microsoft-com:asm.v1">
  <assemblyIdentity version="1.2.0.0" name="ConfereFiscal.App"/>
  <trustInfo xmlns="urn:schemas-microsoft-com:asm.v2">
    <security>
      <requestedPrivileges xmlns="urn:schemas-microsoft-com:asm.v3">
        <requestedExecutionLevel level="asInvoker" uiAccess="false" />
      </requestedPrivileges>
    </security>
  </trustInfo>
  <compatibility xmlns="urn:schemas-microsoft-com:compatibility.v1">
    <application>
      <!-- Windows 7 -->
      <supportedOS Id="{35138b9a-5d96-4fbd-8e2d-a2440225f93a}" />
      <!-- Windows 8 -->
      <supportedOS Id="{4a2f28e3-53b9-4441-ba9c-d69d4a4a6e38}" />
      <!-- Windows 8.1 -->
      <supportedOS Id="{1f676c76-80e1-4239-95bb-83d0f6d0da78}" />
      <!-- Windows 10 e Windows 11 -->
      <supportedOS Id="{8e0f7a12-bfb3-4fe8-b9a5-48fd50a15a9a}" />
    </application>
  </compatibility>
</assembly>
"""
    manifest_path.write_text(manifest_xml, encoding="utf-8")


def compile_csharp(
    cs_code: str,
    output_exe: Path,
    icon_path: Path,
    manifest_path: Path,
    winexe: bool = True,
    resources: list = None,
    references: list = None,
) -> None:
    if not CSC_PATH.exists():
        raise RuntimeError(f"Compilador C# nativo do Windows não encontrado em: {CSC_PATH}")

    cs_file = BUILD_STAGE_DIR / f"{output_exe.stem}.cs"
    cs_file.write_text(cs_code, encoding="utf-8-sig")

    target_type = "winexe" if winexe else "exe"
    cmd = [
        str(CSC_PATH),
        "/nologo",
        "/optimize+",
        f"/target:{target_type}",
        f"/out:{output_exe}",
        f"/win32icon:{icon_path}",
        f"/win32manifest:{manifest_path}",
    ]
    for ref in (references or []):
        cmd.append(f"/reference:{ref}")
    for res_path, res_name in (resources or []):
        cmd.append(f"/resource:{res_path},{res_name}")
    cmd.append(str(cs_file))

    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"Erro ao compilar {output_exe.name}:\nSTDOUT: {proc.stdout}\nSTDERR: {proc.stderr}")


def build_launchers_and_uninstaller(stage_dir: Path, icon_path: Path, manifest_path: Path) -> None:
    """
    Compila os executáveis nativos que ficam dentro da pasta instalada:
      1. ConfereFiscal.exe (Abre o Dashboard Desktop Python com suporte Win 7/8/8.1/10/11)
      2. ConfereFiscal_Web.exe (Inicia o Dashboard Web Local na porta 5000 e abre o navegador)
      3. Desinstalar_ConfereFiscal.exe (Remove atalhos, registro e arquivos da instalação)
    """
    desktop_launcher_cs = r"""
using System;
using System.IO;
using System.Diagnostics;
using System.Windows.Forms;

namespace ConfereFiscalLauncher {
    static class Program {
        [STAThread]
        static void Main(string[] args) {
            string appDir = AppDomain.CurrentDomain.BaseDirectory.TrimEnd('\\', '/');
            string runtimeDir = Path.Combine(appDir, "runtime");
            string pywExe = Path.Combine(runtimeDir, "pythonw.exe");
            string pyExe = Path.Combine(runtimeDir, "python.exe");
            string targetPy = File.Exists(pywExe) ? pywExe : pyExe;

            if (!File.Exists(targetPy)) {
                MessageBox.Show(
                    "Runtime Python embutido não encontrado em:\n" + targetPy + "\n\nReinstale o ConfereFiscal.",
                    "ConfereFiscal - Erro de Inicialização",
                    MessageBoxButtons.OK,
                    MessageBoxIcon.Error
                );
                return;
            }

            ProcessStartInfo psi = new ProcessStartInfo();
            psi.FileName = targetPy;
            psi.Arguments = "-m python_app.desktop_app";
            psi.WorkingDirectory = appDir;
            psi.UseShellExecute = false;
            psi.CreateNoWindow = true;
            psi.EnvironmentVariables["PYTHONPATH"] = appDir;
            psi.EnvironmentVariables["PYTHONNOUSERSITE"] = "1";
            psi.EnvironmentVariables["TCL_LIBRARY"] = Path.Combine(runtimeDir, "tcl", "tcl8.6");
            psi.EnvironmentVariables["TK_LIBRARY"] = Path.Combine(runtimeDir, "tcl", "tk8.6");

            string oldPath = Environment.GetEnvironmentVariable("PATH") ?? "";
            string extraPath = runtimeDir + ";" + Path.Combine(runtimeDir, "DLLs") + ";" + Path.Combine(runtimeDir, "firebird_5_0") + ";" + Path.Combine(runtimeDir, "firebird_2_5");
            psi.EnvironmentVariables["PATH"] = extraPath + ";" + oldPath;

            try {
                Process.Start(psi);
            } catch (Exception ex) {
                MessageBox.Show("Falha ao iniciar o ConfereFiscal:\n" + ex.Message, "ConfereFiscal", MessageBoxButtons.OK, MessageBoxIcon.Error);
            }
        }
    }
}
"""
    compile_csharp(
        desktop_launcher_cs,
        stage_dir / "ConfereFiscal.exe",
        icon_path,
        manifest_path,
        winexe=True,
        references=["System.dll", "System.Windows.Forms.dll"],
    )

    web_launcher_cs = r"""
using System;
using System.IO;
using System.Diagnostics;
using System.Threading;
using System.Windows.Forms;

namespace ConfereFiscalWebLauncher {
    static class Program {
        [STAThread]
        static void Main(string[] args) {
            string appDir = AppDomain.CurrentDomain.BaseDirectory.TrimEnd('\\', '/');
            string runtimeDir = Path.Combine(appDir, "runtime");
            string pyExe = Path.Combine(runtimeDir, "python.exe");

            if (!File.Exists(pyExe)) {
                MessageBox.Show(
                    "Runtime Python embutido não encontrado em:\n" + pyExe,
                    "ConfereFiscal Web - Erro",
                    MessageBoxButtons.OK,
                    MessageBoxIcon.Error
                );
                return;
            }

            ProcessStartInfo psi = new ProcessStartInfo();
            psi.FileName = pyExe;
            psi.Arguments = "-m python_app.web_server";
            psi.WorkingDirectory = appDir;
            psi.UseShellExecute = false;
            psi.CreateNoWindow = false;
            psi.EnvironmentVariables["PYTHONPATH"] = appDir;
            psi.EnvironmentVariables["PYTHONNOUSERSITE"] = "1";

            string oldPath = Environment.GetEnvironmentVariable("PATH") ?? "";
            string extraPath = runtimeDir + ";" + Path.Combine(runtimeDir, "DLLs") + ";" + Path.Combine(runtimeDir, "firebird_5_0") + ";" + Path.Combine(runtimeDir, "firebird_2_5");
            psi.EnvironmentVariables["PATH"] = extraPath + ";" + oldPath;

            try {
                Process proc = Process.Start(psi);
                Thread.Sleep(900);
                Process.Start("http://127.0.0.1:5000");
                if (proc != null) {
                    proc.WaitForExit();
                }
            } catch (Exception ex) {
                MessageBox.Show("Falha ao iniciar Servidor Web ConfereFiscal:\n" + ex.Message, "ConfereFiscal Web", MessageBoxButtons.OK, MessageBoxIcon.Error);
            }
        }
    }
}
"""
    compile_csharp(
        web_launcher_cs,
        stage_dir / "ConfereFiscal_Web.exe",
        icon_path,
        manifest_path,
        winexe=False,
        references=["System.dll", "System.Windows.Forms.dll"],
    )

    uninstaller_cs = r"""
using System;
using System.IO;
using System.Diagnostics;
using System.Windows.Forms;
using Microsoft.Win32;

namespace ConfereFiscalUninstaller {
    static class Program {
        [STAThread]
        static void Main(string[] args) {
            bool silent = false;
            foreach (string a in args) {
                if (a.Equals("/SILENT", StringComparison.OrdinalIgnoreCase) || a.Equals("/S", StringComparison.OrdinalIgnoreCase)) {
                    silent = true;
                }
            }

            string appDir = AppDomain.CurrentDomain.BaseDirectory.TrimEnd('\\', '/');
            if (!silent) {
                DialogResult dr = MessageBox.Show(
                    "Deseja desinstalar o ConfereFiscal desta máquina?\n\nPasta: " + appDir,
                    "Desinstalar ConfereFiscal",
                    MessageBoxButtons.YesNo,
                    MessageBoxIcon.Question
                );
                if (dr != DialogResult.Yes) return;
            }

            bool removeDb = false;
            if (!silent) {
                DialogResult dbDr = MessageBox.Show(
                    "Deseja também excluir o banco de dados local (dados_locais/confere_fiscal_local.db)?\n\nClique em 'Não' para preservar seus dados locais.",
                    "Banco de Dados Local",
                    MessageBoxButtons.YesNo,
                    MessageBoxIcon.Warning
                );
                removeDb = (dbDr == DialogResult.Yes);
            }

            try {
                string desktop = Environment.GetFolderPath(Environment.SpecialFolder.DesktopDirectory);
                string startMenu = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.Programs), "ConfereFiscal");
                string lnk1 = Path.Combine(desktop, "ConfereFiscal.lnk");
                string lnk2 = Path.Combine(desktop, "ConfereFiscal Web.lnk");
                if (File.Exists(lnk1)) File.Delete(lnk1);
                if (File.Exists(lnk2)) File.Delete(lnk2);
                if (Directory.Exists(startMenu)) Directory.Delete(startMenu, true);

                Registry.CurrentUser.DeleteSubKeyTree(@"Software\Microsoft\Windows\CurrentVersion\Uninstall\ConfereFiscal", false);
            } catch {}

            string cmdArgs;
            if (removeDb) {
                cmdArgs = "/C ping 127.0.0.1 -n 2 > nul & rmdir /s /q \"" + appDir + "\"";
            } else {
                cmdArgs = "/C ping 127.0.0.1 -n 2 > nul & rmdir /s /q \"" + Path.Combine(appDir, "runtime") + "\" & rmdir /s /q \"" + Path.Combine(appDir, "python_app") + "\" & rmdir /s /q \"" + Path.Combine(appDir, "src") + "\" & del /f /q \"" + Path.Combine(appDir, "*.exe") + "\" \"" + Path.Combine(appDir, "*.bat") + "\"";
            }

            ProcessStartInfo psi = new ProcessStartInfo("cmd.exe", cmdArgs);
            psi.WindowStyle = ProcessWindowStyle.Hidden;
            psi.CreateNoWindow = true;
            psi.UseShellExecute = false;
            Process.Start(psi);

            if (!silent) {
                MessageBox.Show("ConfereFiscal foi removido com sucesso.", "Desinstalação Concluída", MessageBoxButtons.OK, MessageBoxIcon.Information);
            }
        }
    }
}
"""
    compile_csharp(
        uninstaller_cs,
        stage_dir / "Desinstalar_ConfereFiscal.exe",
        icon_path,
        manifest_path,
        winexe=True,
        references=["System.dll", "System.Windows.Forms.dll"],
    )


def copy_portable_python_runtime(runtime_dest: Path) -> None:
    """
    Copia o runtime Python 3.12 essencial + todos os complementos de sistema para rodar em
    Windows 7, Windows 8, Windows 8.1, Windows 10 e Windows 11:
      - Universal CRT (ucrtbase.dll, api-ms-win-crt-*.dll)
      - VC++ Runtime (vcruntime140.dll, vcruntime140_1.dll, msvcp140.dll, concrt140.dll)
      - Bibliotecas clientes Firebird 2.5 (ODS 11.2) e Firebird 5.0 (ODS 13.1)
    """
    runtime_dest.mkdir(parents=True, exist_ok=True)

    root_files = [
        "python.exe",
        "pythonw.exe",
        "python3.dll",
        "python312.dll",
        "vcruntime140.dll",
        "vcruntime140_1.dll",
        "LICENSE.txt",
    ]
    for fname in root_files:
        src = PYTHON_BASE_DIR / fname
        if src.exists():
            shutil.copy2(src, runtime_dest / fname)

    # Complementos Universal CRT e VC++ para Windows 7, 8 e 8.1
    if SYSTEM32_DIR.exists():
        ucrt_patterns = ["ucrtbase.dll", "msvcp140.dll", "concrt140.dll", "vccorlib140.dll"]
        for fname in ucrt_patterns:
            s_dll = SYSTEM32_DIR / fname
            if s_dll.exists():
                shutil.copy2(s_dll, runtime_dest / fname)
        for api_dll in SYSTEM32_DIR.glob("api-ms-win-*.dll"):
            try:
                shutil.copy2(api_dll, runtime_dest / api_dll.name)
            except Exception:
                pass

    # Complementos de cliente Firebird 2.5 e 5.0 para conexão local/remota em qualquer máquina
    if FB25_CLIENT_DIR.exists():
        fb25_dest = runtime_dest / "firebird_2_5"
        fb25_dest.mkdir(parents=True, exist_ok=True)
        for fname in ["fbclient.dll", "icudt30.dll", "icuin30.dll", "icuuc30.dll", "msvcp80.dll", "msvcr80.dll", "firebird.msg"]:
            src = FB25_CLIENT_DIR / fname
            if src.exists():
                shutil.copy2(src, fb25_dest / fname)

    if FB50_CLIENT_DIR.exists():
        fb50_dest = runtime_dest / "firebird_5_0"
        fb50_dest.mkdir(parents=True, exist_ok=True)
        for fname in ["fbclient.dll", "fbclient5.dll", "icudt63.dll", "icuin63.dll", "icuuc63.dll", "msvcp80.dll", "msvcr80.dll"]:
            src = FB50_CLIENT_DIR / fname
            if src.exists():
                shutil.copy2(src, fb50_dest / fname)
        # Também disponibiliza fbclient.dll na raiz do runtime
        if (FB50_CLIENT_DIR / "fbclient.dll").exists():
            shutil.copy2(FB50_CLIENT_DIR / "fbclient.dll", runtime_dest / "fbclient.dll")

    # 4. Utilitário UnRAR embarcado para extração nativa de pacotes .RAR (RAR4 e RAR5) em Windows 7, 8, 8.1, 10 e 11
    for unrar_src in [
        Path(r"C:\Program Files\WinRAR\UnRAR.exe"),
        Path(r"C:\Program Files (x86)\WinRAR\UnRAR.exe"),
    ]:
        if unrar_src.exists():
            shutil.copy2(unrar_src, runtime_dest / "UnRAR.exe")
            break

    # Copia DLLs (.pyd e .dll como _sqlite3, sqlite3, _tkinter, tcl86t, tk86t, etc.)
    shutil.copytree(PYTHON_BASE_DIR / "DLLs", runtime_dest / "DLLs", dirs_exist_ok=True)

    # Copia Tcl/Tk para interface gráfica Tkinter
    shutil.copytree(PYTHON_BASE_DIR / "tcl", runtime_dest / "tcl", dirs_exist_ok=True)

    # Copia biblioteca padrão (Lib), ignorando pastas pesadas/desnecessárias
    excluded_lib_dirs = {
        "site-packages",
        "test",
        "tests",
        "__pycache__",
        "idlelib",
        "ensurepip",
        "turtledemo",
        "pydoc_data",
        "lib2to3",
    }

    def _ignore_lib(directory: str, contents: list) -> list:
        ignored = []
        for item in contents:
            if item in excluded_lib_dirs or item.endswith(".pyc"):
                ignored.append(item)
        return ignored

    shutil.copytree(
        PYTHON_BASE_DIR / "Lib",
        runtime_dest / "Lib",
        ignore=_ignore_lib,
        dirs_exist_ok=True,
    )


def stage_application_files(stage_dir: Path, icon_path: Path, manifest_path: Path) -> None:
    """
    Monta toda a árvore do aplicativo ConfereFiscal pronta para distribuição.
    """
    if stage_dir.exists():
        shutil.rmtree(stage_dir, ignore_errors=True)
    stage_dir.mkdir(parents=True, exist_ok=True)

    print("  [1/5] Empacotando runtime Python + UCRT/VC++ (Win 7/8/8.1/10/11) + Drivers Firebird 2.5/5.0...")
    copy_portable_python_runtime(stage_dir / "runtime")

    print("  [2/5] Copiando módulos python_app, interface web, ícone e exemplos...")
    shutil.copytree(
        ROOT_DIR / "python_app",
        stage_dir / "python_app",
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
        dirs_exist_ok=True,
    )

    shutil.copytree(ROOT_DIR / "src" / "public", stage_dir / "src" / "public", dirs_exist_ok=True)

    if (ROOT_DIR / "exemplos").exists():
        shutil.copytree(ROOT_DIR / "exemplos", stage_dir / "exemplos", dirs_exist_ok=True)

    if (ROOT_DIR / "docs").exists():
        shutil.copytree(ROOT_DIR / "docs", stage_dir / "docs", dirs_exist_ok=True)
    for doc_file in ["README.md", "requirements.txt", "version.json"]:
        if (ROOT_DIR / doc_file).exists():
            shutil.copy2(ROOT_DIR / doc_file, stage_dir / doc_file)

    dados_dir = stage_dir / "dados_locais"
    dados_dir.mkdir(parents=True, exist_ok=True)
    if (ROOT_DIR / "dados_locais" / "confere_fiscal_local.db").exists():
        shutil.copy2(
            ROOT_DIR / "dados_locais" / "confere_fiscal_local.db",
            dados_dir / "confere_fiscal_local.db",
        )

    # Copia tanto ConfereFiscal.ico quanto instalador/icone.png para o pacote
    shutil.copy2(icon_path, stage_dir / "ConfereFiscal.ico")
    if SOURCE_ICON_PNG.exists():
        shutil.copy2(SOURCE_ICON_PNG, stage_dir / "icone.png")
        (stage_dir / "instalador").mkdir(parents=True, exist_ok=True)
        shutil.copy2(SOURCE_ICON_PNG, stage_dir / "instalador" / "icone.png")

    print("  [3/5] Compilando executáveis nativos (ConfereFiscal.exe, ConfereFiscal_Web.exe, Desinstalar_ConfereFiscal.exe)...")
    build_launchers_and_uninstaller(stage_dir, icon_path, manifest_path)


def create_zip_from_stage(stage_dir: Path, zip_path: Path) -> None:
    if zip_path.exists():
        zip_path.unlink()
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for file_path in stage_dir.rglob("*"):
            if file_path.is_file():
                rel_path = file_path.relative_to(stage_dir).as_posix()
                zf.write(file_path, rel_path)


def build_self_contained_setup_exe(payload_zip: Path, setup_exe_path: Path, icon_path: Path, manifest_path: Path) -> None:
    """
    Compila o instalador gráfico nativo do Windows (Instalador_ConfereFiscal_Setup.exe)
    compatível com Windows 7, 8, 8.1, 10 e 11 (.NET Framework 4.0+).
    """
    installer_cs = r"""
using System;
using System.IO;
using System.IO.Compression;
using System.Reflection;
using System.Threading;
using System.Diagnostics;
using System.Drawing;
using System.Windows.Forms;
using Microsoft.Win32;

namespace ConfereFiscalSetup {
    public class SetupForm : Form {
        private TextBox txtInstallDir;
        private CheckBox chkDesktopShortcut;
        private CheckBox chkStartMenuShortcut;
        private CheckBox chkPreserveDatabase;
        private CheckBox chkLaunchAfter;
        private ProgressBar progressBar;
        private Label lblStatus;
        private TextBox txtLog;
        private Button btnInstall;
        private Button btnCancel;
        private Button btnBrowse;

        public SetupForm() {
            this.Text = "Instalador ConfereFiscal v1.2.0 — Compatível com Windows 7, 8, 8.1, 10 e 11";
            this.Size = new Size(670, 530);
            this.FormBorderStyle = FormBorderStyle.FixedDialog;
            this.MaximizeBox = false;
            this.StartPosition = FormStartPosition.CenterScreen;
            this.BackColor = Color.FromArgb(241, 245, 249);
            this.Font = new Font("Segoe UI", 9.5f, FontStyle.Regular);

            try {
                this.Icon = Icon.ExtractAssociatedIcon(Application.ExecutablePath);
            } catch {}

            Panel header = new Panel();
            header.Dock = DockStyle.Top;
            header.Height = 78;
            header.BackColor = Color.FromArgb(15, 23, 42);

            Label lblTitle = new Label();
            lblTitle.Text = "ConfereFiscal — Auditoria Fiscal Contábil (NF-e 55 / NFC-e 65)";
            lblTitle.ForeColor = Color.White;
            lblTitle.Font = new Font("Segoe UI", 13.5f, FontStyle.Bold);
            lblTitle.AutoSize = true;
            lblTitle.Location = new Point(20, 14);
            header.Controls.Add(lblTitle);

            Label lblSub = new Label();
            lblSub.Text = "Inclui Runtime Python + UCRT/VC++ + Drivers Firebird 2.5/5.0 (Pronto para Windows 7, 8, 8.1, 10 e 11)";
            lblSub.ForeColor = Color.FromArgb(148, 163, 184);
            lblSub.Font = new Font("Segoe UI", 8.6f, FontStyle.Regular);
            lblSub.AutoSize = true;
            lblSub.Location = new Point(22, 44);
            header.Controls.Add(lblSub);

            this.Controls.Add(header);

            Panel body = new Panel();
            body.Location = new Point(18, 92);
            body.Size = new Size(618, 336);
            body.BackColor = Color.White;
            body.BorderStyle = BorderStyle.FixedSingle;

            Label lblDir = new Label();
            lblDir.Text = "Pasta de destino da instalação (não requer privilégios de Administrador):";
            lblDir.Font = new Font("Segoe UI", 9.5f, FontStyle.Bold);
            lblDir.AutoSize = true;
            lblDir.Location = new Point(16, 14);
            body.Controls.Add(lblDir);

            string defaultDir = Path.Combine(
                Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
                "Programs",
                "ConfereFiscal"
            );

            txtInstallDir = new TextBox();
            txtInstallDir.Text = defaultDir;
            txtInstallDir.Location = new Point(16, 40);
            txtInstallDir.Size = new Size(475, 26);
            body.Controls.Add(txtInstallDir);

            btnBrowse = new Button();
            btnBrowse.Text = "Procurar...";
            btnBrowse.Location = new Point(500, 38);
            btnBrowse.Size = new Size(98, 28);
            btnBrowse.Click += OnBrowseClick;
            body.Controls.Add(btnBrowse);

            chkDesktopShortcut = new CheckBox();
            chkDesktopShortcut.Text = "Criar atalhos na Área de Trabalho (ConfereFiscal Desktop e ConfereFiscal Web)";
            chkDesktopShortcut.Checked = true;
            chkDesktopShortcut.AutoSize = true;
            chkDesktopShortcut.Location = new Point(16, 78);
            body.Controls.Add(chkDesktopShortcut);

            chkStartMenuShortcut = new CheckBox();
            chkStartMenuShortcut.Text = "Criar pasta e atalhos no Menu Iniciar do Windows";
            chkStartMenuShortcut.Checked = true;
            chkStartMenuShortcut.AutoSize = true;
            chkStartMenuShortcut.Location = new Point(16, 104);
            body.Controls.Add(chkStartMenuShortcut);

            chkPreserveDatabase = new CheckBox();
            chkPreserveDatabase.Text = "Preservar banco de dados local existente (dados_locais/confere_fiscal_local.db) se já instalado";
            chkPreserveDatabase.Checked = true;
            chkPreserveDatabase.AutoSize = true;
            chkPreserveDatabase.Location = new Point(16, 130);
            body.Controls.Add(chkPreserveDatabase);

            chkLaunchAfter = new CheckBox();
            chkLaunchAfter.Text = "Iniciar o Dashboard ConfereFiscal automaticamente ao concluir a instalação";
            chkLaunchAfter.Checked = true;
            chkLaunchAfter.AutoSize = true;
            chkLaunchAfter.Location = new Point(16, 156);
            body.Controls.Add(chkLaunchAfter);

            lblStatus = new Label();
            lblStatus.Text = "Pronto para instalar em Windows 7 / 8 / 8.1 / 10 / 11. Clique em 'Instalar Agora'.";
            lblStatus.Font = new Font("Segoe UI", 9f, FontStyle.Bold);
            lblStatus.ForeColor = Color.FromArgb(30, 64, 175);
            lblStatus.AutoSize = true;
            lblStatus.Location = new Point(16, 188);
            body.Controls.Add(lblStatus);

            progressBar = new ProgressBar();
            progressBar.Location = new Point(16, 212);
            progressBar.Size = new Size(582, 22);
            progressBar.Minimum = 0;
            progressBar.Maximum = 100;
            body.Controls.Add(progressBar);

            txtLog = new TextBox();
            txtLog.Multiline = true;
            txtLog.ReadOnly = true;
            txtLog.ScrollBars = ScrollBars.Vertical;
            txtLog.BackColor = Color.FromArgb(248, 250, 252);
            txtLog.Font = new Font("Consolas", 8.5f, FontStyle.Regular);
            txtLog.Location = new Point(16, 242);
            txtLog.Size = new Size(582, 78);
            txtLog.Text = "Pacote autônomo pronto: inclui Runtime Python, UCRT/VC++ para Win 7/8/8.1/10/11, SQLite3, Tkinter e Drivers Firebird 2.5 / 5.0.\r\n";
            body.Controls.Add(txtLog);

            this.Controls.Add(body);

            btnInstall = new Button();
            btnInstall.Text = "Instalar Agora";
            btnInstall.Font = new Font("Segoe UI", 10f, FontStyle.Bold);
            btnInstall.BackColor = Color.FromArgb(37, 99, 235);
            btnInstall.ForeColor = Color.White;
            btnInstall.FlatStyle = FlatStyle.Flat;
            btnInstall.Size = new Size(160, 36);
            btnInstall.Location = new Point(350, 438);
            btnInstall.Click += OnInstallClick;
            this.Controls.Add(btnInstall);

            btnCancel = new Button();
            btnCancel.Text = "Fechar";
            btnCancel.Size = new Size(110, 36);
            btnCancel.Location = new Point(524, 438);
            btnCancel.Click += (s, e) => this.Close();
            this.Controls.Add(btnCancel);
        }

        private void OnBrowseClick(object sender, EventArgs e) {
            using (FolderBrowserDialog fbd = new FolderBrowserDialog()) {
                fbd.Description = "Selecione a pasta onde o ConfereFiscal será instalado:";
                fbd.SelectedPath = txtInstallDir.Text;
                if (fbd.ShowDialog() == DialogResult.OK) {
                    txtInstallDir.Text = fbd.SelectedPath;
                }
            }
        }

        private void AppendLog(string msg) {
            if (this.InvokeRequired) {
                this.Invoke(new Action<string>(AppendLog), msg);
                return;
            }
            txtLog.AppendText("[" + DateTime.Now.ToString("HH:mm:ss") + "] " + msg + "\r\n");
        }

        private void UpdateProgress(int pct, string status) {
            if (this.InvokeRequired) {
                this.Invoke(new Action<int, string>(UpdateProgress), pct, status);
                return;
            }
            progressBar.Value = Math.Max(0, Math.Min(100, pct));
            lblStatus.Text = status;
        }

        private void OnInstallClick(object sender, EventArgs e) {
            string targetDir = txtInstallDir.Text.Trim();
            if (string.IsNullOrEmpty(targetDir)) {
                MessageBox.Show("Informe um diretório válido para instalação.", "Atenção", MessageBoxButtons.OK, MessageBoxIcon.Warning);
                return;
            }

            btnInstall.Enabled = false;
            btnBrowse.Enabled = false;
            txtInstallDir.ReadOnly = true;

            bool createDesktop = chkDesktopShortcut.Checked;
            bool createStartMenu = chkStartMenuShortcut.Checked;
            bool preserveDb = chkPreserveDatabase.Checked;
            bool launchAfter = chkLaunchAfter.Checked;

            Thread worker = new Thread(() => {
                try {
                    InstallerEngine.RunInstallation(
                        targetDir,
                        createDesktop,
                        createStartMenu,
                        preserveDb,
                        UpdateProgress,
                        AppendLog
                    );

                    this.Invoke(new Action(() => {
                        btnCancel.Text = "Concluir";
                        MessageBox.Show(
                            "O ConfereFiscal foi instalado com sucesso em:\n" + targetDir + "\n\n" +
                            "Você pode iniciá-lo pelo atalho 'ConfereFiscal' na Área de Trabalho ou no Menu Iniciar.",
                            "Instalação Concluída com Sucesso",
                            MessageBoxButtons.OK,
                            MessageBoxIcon.Information
                        );
                        if (launchAfter) {
                            string exePath = Path.Combine(targetDir, "ConfereFiscal.exe");
                            if (File.Exists(exePath)) {
                                ProcessStartInfo psi = new ProcessStartInfo(exePath);
                                psi.WorkingDirectory = targetDir;
                                Process.Start(psi);
                            }
                        }
                        this.Close();
                    }));
                } catch (Exception ex) {
                    this.Invoke(new Action(() => {
                        btnInstall.Enabled = true;
                        btnBrowse.Enabled = true;
                        txtInstallDir.ReadOnly = false;
                        UpdateProgress(0, "Erro durante a instalação.");
                        AppendLog("ERRO: " + ex.Message);
                        MessageBox.Show("Erro durante a instalação:\n" + ex.Message, "Erro na Instalação", MessageBoxButtons.OK, MessageBoxIcon.Error);
                    }));
                }
            });
            worker.IsBackground = true;
            worker.Start();
        }
    }

    public static class InstallerEngine {
        public static void RunInstallation(
            string targetDir,
            bool createDesktop,
            bool createStartMenu,
            bool preserveDb,
            Action<int, string> onProgress,
            Action<string> onLog
        ) {
            onProgress(5, "Preparando diretório de destino...");
            onLog("Criando diretório: " + targetDir);
            Directory.CreateDirectory(targetDir);

            string existingDbPath = Path.Combine(targetDir, "dados_locais", "confere_fiscal_local.db");
            byte[] backupDbBytes = null;
            if (preserveDb && File.Exists(existingDbPath)) {
                onLog("Preservando banco de dados local existente (confere_fiscal_local.db)...");
                backupDbBytes = File.ReadAllBytes(existingDbPath);
            }

            onProgress(10, "Carregando pacote comprimido integrado...");
            Assembly asm = Assembly.GetExecutingAssembly();
            using (Stream resStream = asm.GetManifestResourceStream("payload.zip")) {
                if (resStream == null) {
                    throw new Exception("Recurso interno payload.zip não encontrado no executável do instalador.");
                }

                using (ZipArchive archive = new ZipArchive(resStream, ZipArchiveMode.Read)) {
                    int total = archive.Entries.Count;
                    int current = 0;
                    onLog("Extraindo " + total + " arquivos do pacote autônomo (Win 7/8/8.1/10/11)...");

                    byte[] copyBuf = new byte[65536];
                    foreach (ZipArchiveEntry entry in archive.Entries) {
                        current++;
                        string relPath = entry.FullName.Replace('/', Path.DirectorySeparatorChar);
                        string destPath = Path.Combine(targetDir, relPath);

                        if (string.IsNullOrEmpty(entry.Name)) {
                            Directory.CreateDirectory(destPath);
                            continue;
                        }

                        string parentDir = Path.GetDirectoryName(destPath);
                        if (!string.IsNullOrEmpty(parentDir)) {
                            Directory.CreateDirectory(parentDir);
                        }

                        using (Stream inStream = entry.Open())
                        using (FileStream outStream = new FileStream(destPath, FileMode.Create, FileAccess.Write, FileShare.None)) {
                            int read;
                            while ((read = inStream.Read(copyBuf, 0, copyBuf.Length)) > 0) {
                                outStream.Write(copyBuf, 0, read);
                            }
                        }

                        if (current % 150 == 0 || current == total) {
                            int pct = 10 + (int)((current * 75.0) / Math.Max(1, total));
                            onProgress(pct, "Extraindo arquivos (" + current + " de " + total + ")...");
                        }
                    }
                }
            }

            if (backupDbBytes != null) {
                Directory.CreateDirectory(Path.GetDirectoryName(existingDbPath));
                File.WriteAllBytes(existingDbPath, backupDbBytes);
                onLog("Banco de dados existente restaurado com sucesso.");
            }

            if (createDesktop) {
                onProgress(88, "Criando atalhos na Área de Trabalho...");
                string desktopDir = Environment.GetFolderPath(Environment.SpecialFolder.DesktopDirectory);
                CreateShortcut(
                    Path.Combine(desktopDir, "ConfereFiscal.lnk"),
                    Path.Combine(targetDir, "ConfereFiscal.exe"),
                    targetDir,
                    "ConfereFiscal — Dashboard de Conciliação e Auditoria Fiscal Contábil",
                    Path.Combine(targetDir, "ConfereFiscal.ico")
                );
                CreateShortcut(
                    Path.Combine(desktopDir, "ConfereFiscal Web.lnk"),
                    Path.Combine(targetDir, "ConfereFiscal_Web.exe"),
                    targetDir,
                    "ConfereFiscal — Dashboard Web Local (Porta 5000)",
                    Path.Combine(targetDir, "ConfereFiscal.ico")
                );
                onLog("Atalhos criados na Área de Trabalho.");
            }

            if (createStartMenu) {
                onProgress(93, "Criando atalhos no Menu Iniciar...");
                string programsDir = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.Programs), "ConfereFiscal");
                Directory.CreateDirectory(programsDir);
                CreateShortcut(
                    Path.Combine(programsDir, "ConfereFiscal.lnk"),
                    Path.Combine(targetDir, "ConfereFiscal.exe"),
                    targetDir,
                    "ConfereFiscal — Dashboard de Conciliação e Auditoria Fiscal Contábil",
                    Path.Combine(targetDir, "ConfereFiscal.ico")
                );
                CreateShortcut(
                    Path.Combine(programsDir, "ConfereFiscal Web.lnk"),
                    Path.Combine(targetDir, "ConfereFiscal_Web.exe"),
                    targetDir,
                    "ConfereFiscal — Dashboard Web Local (Porta 5000)",
                    Path.Combine(targetDir, "ConfereFiscal.ico")
                );
                CreateShortcut(
                    Path.Combine(programsDir, "Desinstalar ConfereFiscal.lnk"),
                    Path.Combine(targetDir, "Desinstalar_ConfereFiscal.exe"),
                    targetDir,
                    "Desinstalar o ConfereFiscal",
                    Path.Combine(targetDir, "ConfereFiscal.ico")
                );
                onLog("Atalhos criados no Menu Iniciar.");
            }

            onProgress(97, "Registrando aplicativo no Windows...");
            RegisterUninstallEntry(targetDir);
            onLog("Aplicação registrada em Aplicativos Instalados do Windows.");

            onProgress(100, "Instalação concluída com sucesso!");
            onLog("Instalação finalizada em: " + targetDir);
        }

        private static void CreateShortcut(string shortcutPath, string targetPath, string workingDir, string description, string iconPath) {
            try {
                Type shellType = Type.GetTypeFromProgID("WScript.Shell");
                if (shellType == null) return;
                dynamic shell = Activator.CreateInstance(shellType);
                dynamic shortcut = shell.CreateShortcut(shortcutPath);
                shortcut.TargetPath = targetPath;
                shortcut.WorkingDirectory = workingDir;
                shortcut.Description = description;
                if (File.Exists(iconPath)) {
                    shortcut.IconLocation = iconPath;
                }
                shortcut.Save();
            } catch {}
        }

        private static void RegisterUninstallEntry(string targetDir) {
            try {
                using (RegistryKey key = Registry.CurrentUser.CreateSubKey(@"Software\Microsoft\Windows\CurrentVersion\Uninstall\ConfereFiscal")) {
                    if (key != null) {
                        key.SetValue("DisplayName", "ConfereFiscal — Dashboard de Auditoria Fiscal e Contábil");
                        key.SetValue("DisplayVersion", "1.2.0");
                        key.SetValue("Publisher", "ConfereFiscal Local Audit");
                        key.SetValue("InstallLocation", targetDir);
                        key.SetValue("DisplayIcon", Path.Combine(targetDir, "ConfereFiscal.exe"));
                        key.SetValue("UninstallString", "\"" + Path.Combine(targetDir, "Desinstalar_ConfereFiscal.exe") + "\"");
                        key.SetValue("NoModify", 1, RegistryValueKind.DWord);
                        key.SetValue("NoRepair", 1, RegistryValueKind.DWord);
                    }
                }
            } catch {}
        }
    }

    static class Program {
        [STAThread]
        static int Main(string[] args) {
            bool silent = false;
            bool noShortcuts = false;
            string customDir = null;

            foreach (string arg in args) {
                if (arg.Equals("/SILENT", StringComparison.OrdinalIgnoreCase) || arg.Equals("/S", StringComparison.OrdinalIgnoreCase)) {
                    silent = true;
                } else if (arg.Equals("/NOSHORTCUTS", StringComparison.OrdinalIgnoreCase)) {
                    noShortcuts = true;
                } else if (arg.StartsWith("/DIR=", StringComparison.OrdinalIgnoreCase)) {
                    customDir = arg.Substring(5).Trim('"');
                }
            }

            if (silent) {
                string targetDir = customDir;
                if (string.IsNullOrEmpty(targetDir)) {
                    targetDir = Path.Combine(
                        Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
                        "Programs",
                        "ConfereFiscal"
                    );
                }
                try {
                    InstallerEngine.RunInstallation(
                        targetDir,
                        !noShortcuts,
                        !noShortcuts,
                        true,
                        (pct, st) => {},
                        (log) => {}
                    );
                    return 0;
                } catch (Exception ex) {
                    Console.Error.WriteLine("Erro na instalacao silenciosa: " + ex.Message);
                    return 1;
                }
            }

            Application.EnableVisualStyles();
            Application.SetCompatibleTextRenderingDefault(false);
            Application.Run(new SetupForm());
            return 0;
        }
    }
}
"""
    compile_csharp(
        installer_cs,
        setup_exe_path,
        icon_path,
        manifest_path,
        winexe=True,
        resources=[(str(payload_zip), "payload.zip")],
        references=[
            "System.dll",
            "System.Core.dll",
            "System.Drawing.dll",
            "System.Windows.Forms.dll",
            "System.IO.Compression.dll",
            "Microsoft.CSharp.dll",
        ],
    )


def main() -> None:
    print("=======================================================================")
    print("  Construtor do Instalador ConfereFiscal (Windows 7, 8, 8.1, 10 e 11)")
    print("=======================================================================")

    DIST_DIR.mkdir(parents=True, exist_ok=True)
    BUILD_STAGE_DIR.mkdir(parents=True, exist_ok=True)

    icon_path = BUILD_STAGE_DIR / "ConfereFiscal.ico"
    create_app_ico(icon_path)

    manifest_path = BUILD_STAGE_DIR / "app.manifest"
    write_win7_to_win11_manifest(manifest_path)

    # 1. Prepara a árvore completa da aplicação + runtime Python + UCRT/VC++ + Firebird 2.5/5.0 + launchers .exe
    stage_application_files(PORTABLE_STAGE_DIR, icon_path, manifest_path)

    # 2. Gera o pacote ZIP portátil (e que também serve de payload para o instalador .exe)
    import json as _json
    app_version = _json.loads((ROOT_DIR / "version.json").read_text(encoding="utf-8")).get("version", "1.0.0")
    portable_zip_path = DIST_DIR / f"ConfereFiscal_Portatil_v{app_version}.zip"
    print(f"  [4/5] Compactando pacote portátil ({portable_zip_path.name})...")
    create_zip_from_stage(PORTABLE_STAGE_DIR, portable_zip_path)

    # 3. Compila o instalador único Instalador_ConfereFiscal_Setup.exe com o payload embutido
    setup_exe_path = DIST_DIR / "Instalador_ConfereFiscal_Setup.exe"
    print("  [5/5] Compilando instalador único (Instalador_ConfereFiscal_Setup.exe)...")
    build_self_contained_setup_exe(portable_zip_path, setup_exe_path, icon_path, manifest_path)

    # Limpa diretório temporário de staging
    shutil.rmtree(BUILD_STAGE_DIR, ignore_errors=True)

    setup_mb = setup_exe_path.stat().st_size / (1024 * 1024)
    zip_mb = portable_zip_path.stat().st_size / (1024 * 1024)

    print("-----------------------------------------------------------------------")
    print(f"  Ícone oficial usado   : {SOURCE_ICON_PNG}")
    print(f"  Instalador EXE gerado : {setup_exe_path} ({setup_mb:.2f} MB)")
    print(f"  Pacote Portátil ZIP   : {portable_zip_path} ({zip_mb:.2f} MB)")
    print("=======================================================================")


if __name__ == "__main__":
    main()
