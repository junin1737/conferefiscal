"""
Script de Versionamento e Publicação do ConfereFiscal (GitHub: junin1737/conferefiscal).

Uso:
  python scripts/release_version.py [patch|minor|major|X.Y.Z] ["Descrição das alterações"] [--push] [--build]

Exemplos:
  python scripts/release_version.py patch "Ajuste no relatório de CFOP" --push
  python scripts/release_version.py minor "Novo módulo de auditoria" --build --push
"""

import subprocess
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from python_app.version_manager import bump_project_version, get_installed_version_info


def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    flags = {a.lower() for a in sys.argv[1:] if a.startswith("--")}

    if not args:
        info = get_installed_version_info()
        print("=======================================================================")
        print(f"  ConfereFiscal — Controle de Versão ({info.get('repository')})")
        print("=======================================================================")
        print(f"  Versão Atual Instalada : v{info['version']} ({info.get('releaseDate')})")
        print(f"  Repositório GitHub     : {info.get('repositoryUrl')}")
        print("-----------------------------------------------------------------------")
        print("  Para gerar uma nova versão, execute:")
        print('    python scripts/release_version.py patch "Descrição da melhoria" --push')
        print("=======================================================================")
        return

    bump_type = args[0]
    note = args[1] if len(args) > 1 else f"Release ConfereFiscal ({bump_type})"

    updated = bump_project_version(bump_type=bump_type, changelog_note=note)
    new_ver = updated["version"]
    print(f"[OK] Versão atualizada para v{new_ver} em version.json e python_app/__init__.py.")

    if "--build" in flags:
        print("[..] Recompilando instalador Windows (scripts/build_installer.py)...")
        subprocess.run([sys.executable, str(ROOT_DIR / "scripts" / "build_installer.py")], cwd=str(ROOT_DIR), check=True)

    if (ROOT_DIR / ".git").exists():
        subprocess.run(["git", "add", "version.json", "python_app/__init__.py"], cwd=str(ROOT_DIR), check=False)
        subprocess.run(["git", "commit", "-m", f"chore(release): v{new_ver} - {note}"], cwd=str(ROOT_DIR), check=False)
        subprocess.run(["git", "tag", "-f", f"v{new_ver}"], cwd=str(ROOT_DIR), check=False)
        print(f"[OK] Commit e tag git 'v{new_ver}' criados localmente.")

        if "--push" in flags:
            print("[..] Enviando commit e tag para origin (GitHub)...")
            subprocess.run(["git", "push", "-u", "origin", "main", "--tags"], cwd=str(ROOT_DIR), check=False)


if __name__ == "__main__":
    main()
