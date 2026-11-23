# 🚀 Guía Maestra: Servidor VPS + Coolify + Cloudflare + GitHub
### Infraestructura Profesional para BotMK y Sistemas Comerciales Multicliente

Esta guía detalla el paso a paso para montar tu propio centro de datos en la nube. Te permitirá tener corriendo **24/7 tu Bot de BingX**, alojar **sistemas comerciales con bases de datos para clientes** (quioscos, constructoras, tiendas, etc.), y desplegar proyectos automáticamente desde GitHub con dominios propios y SSL gratuito, sin límites ni costos ocultos.

---

## 🗺️ Mapa de la Arquitectura

```text
                       ┌───────────────────────────────┐
                       │     GitHub (Repositorios)     │
                       │  - bot-mk                     │
                       │  - sistema-constructora       │
                       │  - app-quiosco                │
                       └───────────────┬───────────────┘
                                       │ (Git Push Automático)
                                       ▼
                       ┌───────────────────────────────┐
                       │   Cloudflare (Escudo DNS)     │
                       │  - SSL HTTPS Automático       │
                       │  - Protección Anti-DDoS       │
                       │  - Oculta la IP de tu VPS     │
                       └───────────────┬───────────────┘
                                       │
                                       ▼
        ┌─────────────────────────────────────────────────────────────┐
        │                 Hetzner Cloud VPS (Ubuntu 24)                │
        │                                                             │
        │  ┌───────────────────────────────────────────────────────┐  │
        │  │                   Panel COOLIFY                       │  │
        │  │   (Gestión visual de proyectos, bases de datos y SSL)  │  │
        │  └───────┬───────────────────┬───────────────────┬───────┘  │
        │          │                   │                   │          │
        │          ▼                   ▼                   ▼          │
        │   ┌─────────────┐     ┌─────────────┐     ┌─────────────┐   │
        │   │   BotMK     │     │ Constructora│     │ App Quiosco │   │
        │   │  (Python)   │     │ (Web + BD)  │     │ (Web + BD)  │   │
        │   │ bot.tu.com  │     │ con.tu.com  │     │ qui.tu.com  │   │
        │   └─────────────┘     └─────────────┘     └─────────────┘   │
        │                                                             │
        │   ┌──────────────────────────────────────────────────────┐  │
        │   │  Bases de Datos Independientes: PostgreSQL / MySQL    │  │
        │   │  Backups Automáticos Diarios a Google Drive / S3      │  │
        │   └──────────────────────────────────────────────────────┘  │
        └─────────────────────────────────────────────────────────────┘
```

---

## 📋 Requisitos Previos

1. **Una cuenta en Hetzner:** [hetzner.com](https://www.hetzner.com) (El proveedor del servidor).
2. **Una cuenta en Cloudflare:** [cloudflare.com](https://www.cloudflare.com) (Gestión de dominios y seguridad).
3. **Un dominio propio:** Puedes comprarlo en Cloudflare Registrar, Namecheap, Nic.ar o GoDaddy por ~$5 a $10 USD al año (ej: `tuestudio.com` o `tusistemas.net`).
4. **Una cuenta en GitHub:** Donde alojarás los repositorios de código.

---

## Paso 1: Crear el Servidor VPS en Hetzner Cloud

1. Entra a tu consola de Hetzner: [console.hetzner.cloud](https://console.hetzner.cloud).
2. Crea un nuevo proyecto llamado **`Produccion`** o **`Servidores-MK`**.
3. Haz clic en **Add Server** (Añadir Servidor) y configura las siguientes opciones:
   * **Location (Ubicación):** Selecciona **Ashburn, VA (EE.UU.)** (Tiene la mejor latencia hacia Sudamérica y hacia los servidores de BingX y servicios globales).
   * **Image (Sistema Operativo):** Selecciona **Ubuntu 24.04 LTS** (o Ubuntu 22.04 LTS).
   * **Type (Tipo de Servidor):** 
     * Pestaña **Shared vCPU** $\rightarrow$ **x86 (Intel/AMD)**.
     * Plan recomendado: **CPX21** (3 vCPU AMD, 4 GB RAM, 80 GB NVMe por ~€7.50/mes) o **CX22** (2 vCPU, 4 GB RAM por ~€4.50/mes).
     * *Cualquiera de los dos aguanta el Bot y 5 a 8 clientes sin despeinarse.*
   * **Networking:** Deja IPv4 pública activada.
   * **SSH Keys (Claves SSH):**
     * Si no tienes clave SSH, genera una en tu terminal de Windows (PowerShell) escribiendo:
       ```powershell
       ssh-keygen -t ed25519 -C "admin@tudominio.com"
       ```
     * Pulsa Enter a todo. Luego copia el contenido de tu clave pública con:
       ```powershell
       cat ~/.ssh/id_ed25519.pub
       ```
     * Pégala en el panel de Hetzner. *(Si no quieres lidiar con claves ahora, puedes elegir que te envíen una contraseña por email).*
   * **Name:** Nombra a tu servidor: `srv-produccion-01`.
4. Haz clic en **Create & Buy Now**. En menos de 20 segundos el servidor estará listo y te mostrará su **Dirección IP pública** (ejemplo: `168.119.45.123`).

---

## Paso 2: Conexión Inicial y Actualización del Servidor

Abre **PowerShell** o **Windows Terminal** en tu PC y conéctate a tu servidor:

```powershell
ssh root@IP_DE_TU_SERVIDOR
```
*(Si usaste contraseña, te la pedirá aquí; si usaste clave SSH, entrarás directo).*

Una vez dentro de la consola negra de Linux, ejecuta este comando para actualizar todo el sistema a su última versión:

```bash
apt update && apt upgrade -y
```

*(Opcional recomendado)* Configura la zona horaria del servidor (ejemplo Argentina):
```bash
timedatectl set-timezone America/Argentina/Buenos_Aires
```

---

## Paso 3: Instalar Coolify con 1 Solo Comando

Coolify es el software que convierte tu servidor en un panel visual similar a Vercel, Supabase y Render combinados, pero 100% tuyo y sin pagar membresías.

Ejecuta en tu terminal SSH:

```bash
curl -fsSL https://cdn.coollabs.io/coolify/install.sh | bash
```

* El instalador descargará automáticamente Docker y todos los servicios necesarios.
* Tardará entre **2 y 4 minutos**.
* Al finalizar, te mostrará un mensaje indicando que Coolify está listo en el puerto `8000`.

### Acceso al Panel Visual:
1. Abre tu navegador web e ingresa a: `http://IP_DE_TU_SERVIDOR:8000`
2. Registra tu cuenta de **Administrador Maestro** (tu correo y contraseña segura).
3. ¡Listo! Ya estás dentro de tu propio panel de control en la nube.

---

## Paso 4: Configurar Cloudflare (DNS, Dominios y SSL Automático)

Para no tener que recordar números de IP y para que todos tus sistemas tengan certificado seguro **`https://`** automático:

1. Entra a [cloudflare.com](https://dash.cloudflare.com) y añade tu dominio (ej: `tudominio.com`).
2. Configura los DNS de tu proveedor de dominio para que apunten a los servidores de Cloudflare.
3. En la sección **DNS $\rightarrow$ Records** de Cloudflare, crea los siguientes registros de tipo **A**:

| Tipo | Nombre (Subdominio) | Contenido (IP) | Proxy Status |
| :--- | :--- | :--- | :--- |
| **A** | `panel` | `IP_DE_TU_SERVIDOR` | 🟠 Nube naranja (Proxied) |
| **A** | `bot` | `IP_DE_TU_SERVIDOR` | 🟠 Nube naranja (Proxied) |
| **A** | `*.clientes` | `IP_DE_TU_SERVIDOR` | 🟠 Nube naranja (Proxied) |

*(El registro comodín `*.clientes` te permite crear al vuelo URLs como `kiosco.clientes.tudominio.com` o `constructora.clientes.tudominio.com` sin tener que tocar DNS cada vez).*

4. En la pestaña **SSL/TLS** de Cloudflare:
   * Selecciona el modo de cifrado: **Full** o **Full (Strict)**.
   * Activa **Always Use HTTPS** (Siempre usar HTTPS).

5. Ahora vincula tu dominio a Coolify:
   * En el panel de Coolify ve a **Settings $\rightarrow$ General $\rightarrow$ Instance Domain**.
   * Escribe: `https://panel.tudominio.com` y guarda.
   * A partir de ahora entrarás a tu panel desde `https://panel.tudominio.com` con candado verde.

---

## Paso 5: Desplegar el Bot de BingX (BotMK) en Coolify

Tu proyecto ya tiene el archivo `Dockerfile` listo para producción.

1. **Subir tu proyecto a GitHub:**
   * En tu PC, sube tu código actual a un repositorio privado de GitHub (ej: `github.com/tu-usuario/BotMK`).
2. **Conectar GitHub con Coolify:**
   * En Coolify, ve a **Sources $\rightarrow$ Add Source $\rightarrow$ GitHub App**.
   * Sigue los pasos para autorizar a Coolify a leer tus repositorios.
3. **Crear el Proyecto del Bot:**
   * En Coolify, ve a **Projects $\rightarrow$ New Project $\rightarrow$ Production**.
   * Haz clic en **+ New Resource $\rightarrow$ Public/Private Repository**.
   * Selecciona tu repositorio `BotMK` y rama `main`.
   * Coolify detectará automáticamente que el proyecto tiene un **Dockerfile**.
4. **Configurar Variables de Entorno (`.env`):**
   * Ve a la pestaña **Environment Variables** en Coolify.
   * Pega tus credenciales de BingX y parámetros operativos:
     ```ini
     BINGX_API_KEY=tu_api_key
     BINGX_SECRET_KEY=tu_secret_key
     IS_DEMO=False
     PORT=8000
     TELEGRAM_ENABLED=True
     TELEGRAM_BOT_TOKEN=tu_token
     TELEGRAM_CHAT_ID=tu_chat_id
     ```
5. **Configurar Dominio del Bot:**
   * En **Domains**, ingresa: `https://bot.tudominio.com`.
6. **Persistencia de la Base de Datos SQLite (Muy Importante):**
   * En la pestaña **Storages (Almacenamiento)**, añade un volumen para que tu historial y configuración no se borren cuando actualices código:
     * **Source Volume:** `bot_data`
     * **Destination Path:** `/app`
7. **Pulsar "Deploy":**
   * Haz clic en el botón azul **Deploy**.
   * Coolify compilará la imagen de Docker, encenderá el bot y configurará el certificado SSL.
   * ¡En 60 segundos tu bot estará operando en vivo en `https://bot.tudominio.com`!

---

## Paso 6: Cómo Crear Nuevos Sistemas Comerciales para Clientes

Supongamos que vas a desarrollar o alojar el **Sistema para la Empresa Constructora**:

### 1. Crear la Base de Datos PostgreSQL del Cliente
1. En Coolify, dentro del proyecto del cliente, haz clic en **+ New Resource $\rightarrow$ Database $\rightarrow$ PostgreSQL**.
2. Asígnale un nombre: `db-constructora`.
3. Coolify creará un contenedor de PostgreSQL aislado y te entregará la URL de conexión interna:
   ```text
   postgresql://postgres:password123@db-constructora:5432/constructora_db
   ```
4. Solo este cliente tendrá acceso a esta base de datos. Nadie más.

### 2. Desplegar la Aplicación Web del Cliente
1. Haz clic en **+ New Resource $\rightarrow$ Git Repository**.
2. Selecciona el repositorio de la constructora (ej: hecho en Next.js, Node, Laravel, Django o FastAPI).
3. En **Environment Variables**, pega la URL de la base de datos que creaste en el paso anterior (`DATABASE_URL=...`).
4. En **Domains**, asigna el dominio que quiera el cliente:
   * `https://constructora.clientes.tudominio.com` o el dominio propio del cliente si te lo compran: `https://gestion.constructoraejemplo.com`.
5. Pulsa **Deploy**.

### 3. Activar Backups Diarios Automáticos
1. Entra a la base de datos `db-constructora` en Coolify.
2. Ve a la pestaña **Backups**.
3. Activa los respaldos automáticos (puedes configurarlo para que se hagan todas las noches a las 03:00 AM).
4. Puedes vincularlo a un bucket de almacenamiento económico (como Cloudflare R2, AWS S3 o Google Drive) para tener copias externas ante cualquier desastre.

---

## Paso 7: Actualizaciones de Código en 1 Clic (CI/CD Automático)

Cada vez que hagas mejoras o agregues funciones al Bot o a la web de un cliente:

1. Modificas el código en tu máquina local con tu IDE.
2. Haces commit y push a GitHub:
   ```bash
   git add .
   git commit -m "Nueva funcion de presupuestos"
   git push origin main
   ```
3. **Coolify recibirá la notificación de GitHub por Webhook, descargará los cambios y compilará la nueva versión automáticamente sin cortar el servicio.**

---

## 💰 Resumen Financiero y de Negocio

| Concepto | Si usas servicios separados (Vercel + Supabase + VPS) | Con este esquema (Hetzner + Coolify + Cloudflare) |
| :--- | :--- | :--- |
| **Costo Bot de Trading** | $5 - $10 / mes | $0 (corre en tu propio VPS) |
| **Cliente 1 (Constructora + BD)** | $20 / mes (Vercel Pro + Supabase Pro) | $0 (usa un contenedor Docker en el VPS) |
| **Cliente 2 (Quiosco + BD)** | $20 / mes | $0 (usa un contenedor Docker en el VPS) |
| **Certificados SSL y Dominios** | Variable / Costo extra | 100% Gratis con Let's Encrypt y Cloudflare |
| **Costo Total Fijo** | **$45 a $70 USD / mes** | **~€7.50 EUR / mes (~$8 USD)** |

### ¿Cuánto le cobras a los clientes?
* **Abono de Mantenimiento y Hosting:** Puedes cobrar entre **$15, $30 y $60 USD al mes a cada cliente** por tener su sistema en línea, con copias de seguridad diarias, soporte y certificado seguro.
* Con solo **1 cliente**, tu infraestructura ya se paga sola y el resto es ganancia 100% neta para tu negocio.
