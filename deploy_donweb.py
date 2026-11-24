"""Despliegue automático de BotMK en el VPS de DonWeb vía SSH."""

from pathlib import Path
import sys
import paramiko


def load_env(env_path: Path) -> dict[str, str]:
    """Carga pares clave=valor desde el archivo .donweb.env."""
    if not env_path.exists():
        raise FileNotFoundError(f"No se encontró {env_path}")
    config: dict[str, str] = {}
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        config[key.strip()] = value.strip()
    return config


def run_remote(client: paramiko.SSHClient, cmd: str) -> tuple[int, str, str]:
    """Ejecuta un comando remoto por SSH y devuelve (exit_code, stdout, stderr)."""
    _, stdout, stderr = client.exec_command(cmd)
    exit_code = stdout.channel.recv_exit_status()
    return (
        exit_code,
        stdout.read().decode(errors="replace").strip(),
        stderr.read().decode(errors="replace").strip(),
    )


def main() -> int:
    """Actualiza el repositorio en el VPS de DonWeb y reinicia botmk.service."""
    cfg = load_env(Path(__file__).resolve().parent / ".donweb.env")
    host = cfg["VPS_HOST"]
    port = int(cfg.get("VPS_PORT", "22"))
    user = cfg["VPS_USER"]
    password = cfg["VPS_PASS"]
    remote_dir = cfg.get("VPS_DIR", "/opt/botmk")
    service = cfg.get("VPS_SERVICE", "botmk.service")

    print(f"Conectando a {user}@{host}:{port}...")
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(host, port=port, username=user, password=password, timeout=20)

    try:
        # Detectar si requirements.txt cambia entre HEAD y origin/main
        check_cmd = (
            f"cd {remote_dir} && git fetch origin main && "
            "git diff --name-only HEAD origin/main"
        )
        code, changed_files, err = run_remote(client, check_cmd)
        if code != 0:
            print(f"Error al consultar Git en el VPS:\n{err}")
            return code

        if not changed_files:
            print("El VPS ya está actualizado con origin/main.")
        else:
            print(f"Archivos actualizados:\n{changed_files}")

        pull_cmd = f"cd {remote_dir} && git pull origin main"
        code, out, err = run_remote(client, pull_cmd)
        if code != 0:
            print(f"Error en git pull:\n{out}\n{err}")
            return code
        if out:
            print(out)

        if "requirements.txt" in changed_files.splitlines():
            print("Detectado cambio en requirements.txt, instalando dependencias...")
            pip_cmd = f"{remote_dir}/venv/bin/pip install -r {remote_dir}/requirements.txt"
            code, out, err = run_remote(client, pip_cmd)
            if code != 0:
                print(f"Error instalando dependencias:\n{err}")
                return code

        print(f"Reiniciando {service}...")
        code, out, err = run_remote(
            client, f"systemctl restart {service} && systemctl is-active {service}"
        )
        if code != 0 or out.splitlines()[-1].strip() != "active":
            print(f"Error reiniciando servicio:\n{out}\n{err}")
            return 1

        print("Deploy completado: botmk.service activo en DonWeb.")
        return 0
    finally:
        client.close()


if __name__ == "__main__":
    sys.exit(main())
