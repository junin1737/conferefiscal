"""
Gerenciador de Versão e Atualização Automática do ConfereFiscal.
Conectado ao repositório oficial no GitHub: https://github.com/junin1737/conferefiscal

Funcionalidades:
  - Leitura da versão instalada localmente (`version.json` / `python_app.__version__`).
  - Consulta da versão publicada no GitHub (`junin1737/conferefiscal`) via:
      1. `raw.githubusercontent.com/junin1737/conferefiscal/{branch}/version.json`
      2. GitHub Releases API (`api.github.com/repos/junin1737/conferefiscal/releases/latest`)
      3. GitHub Contents API (`api.github.com/repos/junin1737/conferefiscal/contents/version.json`)
  - Comparação SemVer (`MAJOR.MINOR.PATCH`) entre versão instalada e versão publicada.
  - Atualização direta pela aplicação (via `git pull` ou download do pacote publicado no GitHub).
  - Utilitário de incremento de versão (`patch`, `minor`, `major`) para publicação de releases.
"""

import base64
import io
import json
import os
import re
import shutil
import ssl
import subprocess
import tempfile
import urllib.error
import urllib.request
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

ROOT_DIR = Path(__file__).resolve().parent.parent
VERSION_FILE_PATH = ROOT_DIR / "version.json"

DEFAULT_GITHUB_REPO = "junin1737/conferefiscal"
DEFAULT_GITHUB_URL = f"https://github.com/{DEFAULT_GITHUB_REPO}"
DEFAULT_BRANCH = "main"


def parse_semver(version_str: str) -> Tuple[int, int, int]:
    """Converte uma string de versão ('v1.1.0', '1.1.0-beta', etc.) em tupla (major, minor, patch)."""
    cleaned = re.sub(r"^[^0-9]*", "", str(version_str or "").strip())
    parts = re.findall(r"\d+", cleaned)
    major = int(parts[0]) if len(parts) > 0 else 0
    minor = int(parts[1]) if len(parts) > 1 else 0
    patch = int(parts[2]) if len(parts) > 2 else 0
    return (major, minor, patch)


def format_semver(version_str: str) -> str:
    """Padroniza uma string de versão como 'X.Y.Z'."""
    maj, min_, pat = parse_semver(version_str)
    if maj == 0 and min_ == 0 and pat == 0:
        return "1.0.0"
    return f"{maj}.{min_}.{pat}"


def compare_versions(installed_ver: str, published_ver: str) -> int:
    """
    Compara duas versões SemVer:
      Retorna  1 se `published_ver` > `installed_ver` (há atualização disponível)
      Retorna  0 se `published_ver` == `installed_ver` (versão atualizada)
      Retorna -1 se `published_ver` < `installed_ver` (versão instalada é mais recente que a publicada)
    """
    inst = parse_semver(installed_ver)
    pub = parse_semver(published_ver)
    if pub > inst:
        return 1
    if pub < inst:
        return -1
    return 0


def get_installed_version_info() -> Dict[str, Any]:
    """Lê os metadados da versão instalada localmente a partir de `version.json`."""
    default_info: Dict[str, Any] = {
        "appName": "ConfereFiscal",
        "version": "1.1.0",
        "channel": "stable",
        "releaseDate": datetime.now().strftime("%Y-%m-%d"),
        "repository": DEFAULT_GITHUB_REPO,
        "repositoryUrl": DEFAULT_GITHUB_URL,
        "branch": DEFAULT_BRANCH,
        "installerAssetName": "Instalador_ConfereFiscal_Setup.exe",
        "portableAssetName": "ConfereFiscal_Portatil_v1.1.0.zip",
        "changelog": [
            "v1.1.0 - Sistema de controle de versão integrado ao GitHub (junin1737/conferefiscal)"
        ],
    }

    if VERSION_FILE_PATH.exists():
        try:
            raw = json.loads(VERSION_FILE_PATH.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                default_info.update(raw)
        except Exception:
            pass
    else:
        try:
            from . import __version__ as pkg_ver

            if pkg_ver:
                default_info["version"] = str(pkg_ver)
        except Exception:
            pass

    default_info["version"] = format_semver(str(default_info.get("version") or "1.1.0"))
    return default_info


def _http_get_json(url: str, timeout: float = 7.0) -> Optional[Dict[str, Any]]:
    """Executa requisição HTTP GET retornando JSON decodificado (com suporte TLS/SSL padrão)."""
    headers = {
        "User-Agent": "ConfereFiscal-Updater/1.2 (+https://github.com/junin1737/conferefiscal)",
        "Accept": "application/json, application/vnd.github+json",
        "Cache-Control": "no-cache",
    }
    req = urllib.request.Request(url, headers=headers, method="GET")
    ctx = ssl.create_default_context()
    with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
        if resp.status != 200:
            return None
        body = resp.read().decode("utf-8", errors="replace")
        return json.loads(body)


def check_published_version(
    repository: Optional[str] = None,
    timeout: float = 7.0,
) -> Dict[str, Any]:
    """
    Verifica a versão publicada no GitHub (`junin1737/conferefiscal`) e compara
    com a versão instalada localmente.
    """
    local_info = get_installed_version_info()
    installed_ver = local_info["version"]
    repo = (repository or local_info.get("repository") or DEFAULT_GITHUB_REPO).strip()
    repo_url = f"https://github.com/{repo}"
    branch = str(local_info.get("branch") or DEFAULT_BRANCH).strip()

    remote_version: Optional[str] = None
    remote_date: Optional[str] = None
    remote_changelog: List[str] = []
    download_url: str = f"https://github.com/{repo}/archive/refs/heads/{branch}.zip"
    installer_download_url: Optional[str] = None
    source_used: str = ""
    errors_seen: List[str] = []

    # 1. Tenta ler `version.json` diretamente da branch principal (main / master) via raw.githubusercontent.com
    ts_buster = int(datetime.now().timestamp())
    for br in (branch, "main", "master"):
        raw_url = f"https://raw.githubusercontent.com/{repo}/{br}/version.json?t={ts_buster}"
        try:
            data = _http_get_json(raw_url, timeout=timeout)
            if isinstance(data, dict) and data.get("version"):
                remote_version = format_semver(str(data["version"]))
                remote_date = str(data.get("releaseDate") or "")
                if isinstance(data.get("changelog"), list):
                    remote_changelog = [str(x) for x in data["changelog"]]
                download_url = f"https://github.com/{repo}/archive/refs/heads/{br}.zip"
                source_used = f"GitHub ({repo}@{br}/version.json)"
                break
        except urllib.error.HTTPError as he:
            errors_seen.append(f"raw/{br}: HTTP {he.code}")
        except Exception as exc:
            errors_seen.append(f"raw/{br}: {exc}")

    # 2. Tenta consultar GitHub Contents API caso o raw tenha falhado por cache/CDN
    if not remote_version:
        contents_url = f"https://api.github.com/repos/{repo}/contents/version.json"
        try:
            c_data = _http_get_json(contents_url, timeout=timeout)
            if isinstance(c_data, dict) and c_data.get("content"):
                decoded = base64.b64decode(c_data["content"]).decode("utf-8", errors="replace")
                parsed = json.loads(decoded)
                if isinstance(parsed, dict) and parsed.get("version"):
                    remote_version = format_semver(str(parsed["version"]))
                    remote_date = str(parsed.get("releaseDate") or "")
                    if isinstance(parsed.get("changelog"), list):
                        remote_changelog = [str(x) for x in parsed["changelog"]]
                    source_used = f"GitHub API ({repo}/contents/version.json)"
        except urllib.error.HTTPError as he:
            errors_seen.append(f"contents_api: HTTP {he.code}")
        except Exception as exc:
            errors_seen.append(f"contents_api: {exc}")

    checked_at = datetime.now().isoformat(timespec="seconds")

    # Caso não tenha sido possível obter `version.json` remoto (ex.: sem internet ou repositório ainda sem commit de version.json)
    if not remote_version:
        return {
            "ok": False,
            "reachable": False,
            "repository": repo,
            "repositoryUrl": repo_url,
            "installedVersion": installed_ver,
            "installedReleaseDate": local_info.get("releaseDate", ""),
            "publishedVersion": None,
            "publishedReleaseDate": None,
            "updateAvailable": False,
            "isUpToDate": True,
            "comparison": 0,
            "statusTitle": f"Versão Instalada: v{installed_ver} (Repositório sem versão publicada ou offline)",
            "statusMessage": (
                f"Não foi possível localizar um arquivo 'version.json' ou Release publicada em {repo_url}.\n"
                f"Verifique sua conexão com a internet ou se o primeiro envio (git push) já foi concluído."
            ),
            "changelog": local_info.get("changelog", []),
            "downloadUrl": download_url,
            "installerDownloadUrl": installer_download_url,
            "checkedAt": checked_at,
            "technicalDetails": "; ".join(errors_seen),
        }

    cmp_res = compare_versions(installed_ver, remote_version)
    update_available = cmp_res > 0
    is_up_to_date = cmp_res <= 0

    if update_available:
        status_title = f"⬆ Nova Versão Disponível: v{remote_version}"
        status_msg = (
            f"Há uma nova versão publicada no repositório oficial ({repo})!\n"
            f"• Versão Instalada nesta máquina: v{installed_ver}\n"
            f"• Versão Publicada no GitHub: v{remote_version} ({remote_date or 'Recente'})"
        )
    elif cmp_res == 0:
        status_title = f"✓ Aplicação Atualizada (v{installed_ver})"
        status_msg = (
            f"Sua instalação já está na versão mais recente publicada no GitHub ({repo}).\n"
            f"• Versão Instalada: v{installed_ver}\n"
            f"• Versão Publicada: v{remote_version}"
        )
    else:
        status_title = f"✓ Versão Local mais Recente (v{installed_ver} > v{remote_version})"
        status_msg = (
            f"A versão instalada localmente (v{installed_ver}) é mais nova que a última versão publicada no GitHub (v{remote_version})."
        )

    return {
        "ok": True,
        "reachable": True,
        "sourceUsed": source_used,
        "repository": repo,
        "repositoryUrl": repo_url,
        "installedVersion": installed_ver,
        "installedReleaseDate": local_info.get("releaseDate", ""),
        "publishedVersion": remote_version,
        "publishedReleaseDate": remote_date,
        "updateAvailable": update_available,
        "isUpToDate": is_up_to_date,
        "comparison": cmp_res,
        "statusTitle": status_title,
        "statusMessage": status_msg,
        "changelog": remote_changelog or local_info.get("changelog", []),
        "downloadUrl": download_url,
        "installerDownloadUrl": installer_download_url,
        "checkedAt": checked_at,
    }


# Pastas/arquivos do pacote publicado que são copiados para a instalação.
# Nunca tocamos em dados do cliente (dados_locais/, .local_data/) nem no runtime Python embutido.
UPDATE_PATHS = ("python_app/", "src/public/", "docs/", "version.json", "README.md")


def _download(url: str, timeout: float = 60.0) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "ConfereFiscal-Updater/1.2", "Cache-Control": "no-cache"})
    with urllib.request.urlopen(req, timeout=timeout, context=ssl.create_default_context()) as resp:
        return resp.read()


def apply_published_update(
    remote_info: Optional[Dict[str, Any]] = None,
    progress_callback: Optional[Callable[[str], None]] = None,
) -> Dict[str, Any]:
    """
    Baixa o código publicado no GitHub (branch principal), confere a versão,
    faz backup dos arquivos atuais e instala a nova versão por cima.
    Os dados locais (dados_locais/, .local_data/) são preservados.
    """
    def _notify(msg: str) -> None:
        if progress_callback:
            try:
                progress_callback(msg)
            except Exception:
                pass

    info = remote_info or check_published_version()
    repo = str(info.get("repository") or DEFAULT_GITHUB_REPO)
    local_info = get_installed_version_info()
    branch = str(local_info.get("branch") or DEFAULT_BRANCH)
    zip_url = str(info.get("downloadUrl") or f"https://github.com/{repo}/archive/refs/heads/{branch}.zip")

    try:
        _notify("Baixando a nova versão...")
        zip_bytes = _download(f"{zip_url}?t={int(datetime.now().timestamp())}")

        _notify("Conferindo o pacote baixado...")
        with zipfile.ZipFile(io.BytesIO(zip_bytes), "r") as zf:
            names = [n for n in zf.namelist() if not n.endswith("/")]
            if not names:
                raise RuntimeError("O pacote baixado está vazio.")
            top = names[0].split("/")[0] + "/" if "/" in names[0] else ""
            files: Dict[str, bytes] = {}
            for member in names:
                rel = member[len(top):] if top and member.startswith(top) else member
                if rel and (rel in UPDATE_PATHS or rel.startswith(tuple(p for p in UPDATE_PATHS if p.endswith("/")))):
                    if ".." in Path(rel).parts:
                        continue
                    files[rel] = zf.read(member)

        if "version.json" not in files or "python_app/desktop_app.py" not in files:
            raise RuntimeError("O pacote baixado não contém os arquivos do aplicativo.")
        new_version = format_semver(json.loads(files["version.json"].decode("utf-8")).get("version", "0.0.0"))
        if compare_versions(local_info["version"], new_version) < 0 and not info.get("forceReinstall"):
            raise RuntimeError(
                f"O pacote publicado (v{new_version}) é mais antigo que a versão instalada (v{local_info['version']})."
            )

        # Backup da versão atual (permite desfazer manualmente se algo der errado)
        _notify("Fazendo backup da versão atual...")
        backup_dir = ROOT_DIR / ".local_data" / "backups" / f"v{local_info['version']}_{datetime.now():%Y%m%d_%H%M%S}"
        backup_dir.mkdir(parents=True, exist_ok=True)
        for rel in files:
            cur = ROOT_DIR / rel
            if cur.exists():
                dst = backup_dir / rel
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(cur, dst)

        _notify("Instalando a nova versão...")
        for rel, data in files.items():
            target = ROOT_DIR / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            tmp = target.with_name(target.name + ".novo")
            tmp.write_bytes(data)
            os.replace(tmp, target)

        # Remove caches antigos para garantir que o Python carregue o código novo
        for cache in (ROOT_DIR / "python_app").rglob("__pycache__"):
            shutil.rmtree(cache, ignore_errors=True)

        return {
            "ok": True,
            "method": "GITHUB_ARCHIVE_ZIP",
            "updatedFilesCount": len(files),
            "updatedToVersion": new_version,
            "backupDir": str(backup_dir),
            "message": f"Atualizado para a versão {new_version}. Reinicie o aplicativo.",
        }
    except Exception as exc:
        return {
            "ok": False,
            "error": str(exc),
            "message": f"Não foi possível atualizar: {exc}\n\nVerifique a internet e tente novamente.",
        }


def bump_project_version(
    bump_type: str = "patch",
    changelog_note: str = "",
) -> Dict[str, Any]:
    """
    Incrementa a versão do projeto (`patch`, `minor` ou `major`) em:
      - `version.json`
      - `python_app/__init__.py`
      - `package.json`
    """
    info = get_installed_version_info()
    maj, min_, pat = parse_semver(info["version"])
    b_type = bump_type.strip().lower()
    if b_type == "major":
        maj, min_, pat = maj + 1, 0, 0
    elif b_type == "minor":
        min_, pat = min_ + 1, 0
    elif re.match(r"^\d+\.\d+\.\d+$", b_type):
        maj, min_, pat = parse_semver(b_type)
    else:
        pat += 1

    new_ver = f"{maj}.{min_}.{pat}"
    today_iso = datetime.now().strftime("%Y-%m-%d")

    info["version"] = new_ver
    info["releaseDate"] = today_iso
    info["portableAssetName"] = f"ConfereFiscal_Portatil_v{new_ver}.zip"

    note_clean = changelog_note.strip() or f"Atualização da aplicação ConfereFiscal v{new_ver}"
    entry_line = f"v{new_ver} - {note_clean}"
    changelog = list(info.get("changelog") or [])
    if not changelog or changelog[0] != entry_line:
        changelog.insert(0, entry_line)
    info["changelog"] = changelog[:25]

    history = list(info.get("history") or [])
    history.insert(0, {"version": new_ver, "date": today_iso, "summary": note_clean})
    info["history"] = history[:25]

    VERSION_FILE_PATH.write_text(json.dumps(info, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    # Atualiza python_app/__init__.py
    init_py = ROOT_DIR / "python_app" / "__init__.py"
    if init_py.exists():
        content = init_py.read_text(encoding="utf-8")
        content = re.sub(r'__version__\s*=\s*"[^"]*"', f'__version__ = "{new_ver}"', content)
        init_py.write_text(content, encoding="utf-8")

    # Atualiza package.json
    pkg_json = ROOT_DIR / "package.json"
    if pkg_json.exists():
        try:
            pkg_data = json.loads(pkg_json.read_text(encoding="utf-8"))
            pkg_data["version"] = new_ver
            pkg_json.write_text(json.dumps(pkg_data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        except Exception:
            pass

    return info
