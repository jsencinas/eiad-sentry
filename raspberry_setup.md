# Conectar una Raspberry Pi a WiFi empresarial (WPA/WPA2 Enterprise) vía SSH

Documentación del proceso completo: qué hicimos, por qué, y cómo repetirlo o arreglarlo si algo falla.

---

## 1. El problema

Raspberry Pi Imager solo permite configurar redes WiFi **personales** (SSID + contraseña compartida). No tiene campos para redes **empresariales** (WPA/WPA2 Enterprise), que necesitan usuario, método EAP, identidad anónima, certificados, etc.

La red del Tec (`Tec`) usa:

| Campo | Valor |
|---|---|
| Seguridad | WPA & WPA2 Enterprise |
| Autenticación | PEAP |
| Identidad anónima | `Tec` |
| Dominio | `wireless.itesm.mx` (no se usa sin certificado CA) |
| Certificado CA | Ninguno ("No CA certificate is required") |
| Versión PEAP | Automática |
| Autenticación interna | MSCHAPv2 |

## 2. La solución en resumen

1. Flashear la Pi con Imager usando el **hotspot del teléfono** como WiFi.
2. Entrar a la Pi por **SSH** desde la laptop (ambas en el hotspot).
3. Instalar **Tailscale** en la Pi y en la laptop, para poder volver a entrar sin importar en qué red esté la Pi.
4. Crear el **perfil de WiFi empresarial** con `nmcli` (script).
5. **Cambiar de red** de la Pi (con un temporizador de seguridad la primera vez).
6. Reconectar por la **IP de Tailscale**.

---

## 3. Conceptos clave

### 3.1 SSH: de qué computadora es la terminal

Cuando haces `ssh jorge@IP`, tu terminal pasa a **controlar la Pi**. Lo que escribes se ejecuta allá. Se nota en el prompt:

- `jorge@envy` → laptop
- `jorge@raspberrio` → Pi

`exit` te regresa a la laptop.

### 3.2 La Pi tiene una sola antena WiFi

Al conectarse a la red empresarial, **deja el hotspot**. La sesión SSH que pasaba por el hotspot muere, y la IP de la Pi cambia (la red empresarial le da otra).

### 3.3 Tailscale

Crea una red privada virtual entre **tus propios dispositivos**:

- Cada dispositivo recibe una IP fija `100.x.y.z` que **no cambia** aunque cambie de red.
- Funciona sobre una interfaz virtual (`tailscale0`), separada de `wlan0`.
- Usa el rango `100.64.0.0/10`, que casi nunca choca con redes reales (`10.x`, `172.16.x`, `192.168.x`).
- Necesita: mismo login en ambos dispositivos y salida a internet en ambos.

No conecta la Pi al WiFi; solo te da una dirección fija para **volver a entrar**.

### 3.4 NetworkManager y perfiles

NetworkManager guarda **perfiles** de conexión. `nmcli connection add` solo **guarda** un perfil; no lo activa. Se activa con `nmcli connection up` o al reiniciar (si tiene `autoconnect`).

Si hay varios perfiles disponibles, gana el de mayor `autoconnect-priority`.

### 3.5 Netplan

El perfil del hotspot se llama `netplan-wlan0-Galaxy S26 Ultra 7FE1`. El prefijo `netplan-wlan0-` indica que lo creó Imager vía cloud-init/netplan. Hay que usar el nombre **completo** con `nmcli`.

---

## 4. Paso a paso

### Paso 0: Flashear la Pi

En Raspberry Pi Imager (con permisos elevados si los pide; mejor el prompt propio de la app que `sudo` a todo):

- Hostname (aquí: `raspberrio`), usuario (`jorge`) y contraseña.
- WiFi: el **hotspot del teléfono** (SSID, contraseña y **país del WiFi**; sin país, la radio puede quedar deshabilitada).
- **Habilitar SSH.**
- Verificar dos veces el dispositivo de destino antes de escribir (para no borrar el disco equivocado).

> En Arch también existe `sudo pacman -S rpi-imager`. El AppImage requiere `chmod +x` y a veces `fuse2`.

### Paso 1: Encontrar la IP de la Pi en el hotspot

- Revisar la lista de dispositivos conectados en el teléfono, o
- Escanear: `nmap -sn 10.53.109.0/24` (usar la subred de tu laptop; se ve con `ip -4 addr`).
- Confirmar con `ping -c 3 IP` y `ip neigh`. La MAC de una Pi empieza con `2c:cf:67` (o similar de Raspberry Pi Trading).

**Errores que ocurrieron:**

| Intento | Problema |
|---|---|
| `ssh jorge@hostname.localhost` | `.localhost` apunta a tu propia laptop. Debe ser `.local` (o usar la IP). |
| `ssh jorge@raspberrio.10.53.109.82` | Mezcla hostname + IP. Se usa uno u otro. |
| `No route to host` | Fue momentáneo (ARP sin resolver, la Pi aún terminaba de arrancar). Reintentar funcionó. |

### Paso 2: Conectar por SSH

```bash
ssh jorge@10.53.109.82
```

### Paso 3: Instalar Tailscale en la Pi (estando en el hotspot)

```bash
curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up
```

Abrir el enlace que imprime y autenticarse. Luego:

```bash
tailscale ip -4      # IP de Tailscale de la Pi
```

En la Pi, el servicio debe estar habilitado para arrancar solo:

```bash
systemctl is-enabled tailscaled     # debe decir "enabled"
```

### Paso 4: Instalar Tailscale en la laptop (Arch)

```bash
sudo pacman -S tailscale
sudo systemctl start tailscaled     # o "enable --now" para que arranque siempre
sudo tailscale up                   # misma cuenta que la Pi
tailscale status                    # debe listar la Pi
```

- `enable --now` = arranca ahora **y** en cada inicio.
- `start` = solo esta sesión.
- Que `tailscaled` corra no significa que estés conectado: eso lo controlan `tailscale up` / `tailscale down`.

Parar y desactivar:

```bash
sudo tailscale down
sudo systemctl disable --now tailscaled
```

### Paso 5: Crear el perfil de WiFi empresarial (script)

Guardar en la Pi como `wifi.sh`:

```bash
#!/bin/bash
set -e

SSID="Tec"
USER_ID='tu_usuario'
PASS='tu_contraseña'

# Borra el perfil si ya existe (evita duplicados)
sudo nmcli connection delete "corp-wifi" 2>/dev/null || true

sudo nmcli connection add type wifi con-name "corp-wifi" ifname wlan0 ssid "$SSID" \
  wifi-sec.key-mgmt wpa-eap \
  802-1x.eap peap \
  802-1x.anonymous-identity "Tec" \
  802-1x.phase2-auth mschapv2 \
  802-1x.identity "$USER_ID" \
  802-1x.password "$PASS" \
  connection.autoconnect yes \
  connection.autoconnect-priority 10

echo "Perfil añadido. Aún no se ha activado, así que tu sesión SSH sigue viva."
```

Ejecutar:

```bash
chmod 700 wifi.sh
./wifi.sh
```

Notas:

- `USER_ID` es el usuario **de la red WiFi** (matrícula o correo institucional), **no** `jorge`.
- Las comillas simples en `PASS` evitan que `$`, `"`, `` ` `` o `\` se interpreten. Una comilla simple dentro se escribe `'\''`.
- No se usan `ca-cert` ni `domain-suffix-match`, porque la red no pide certificado.
- El nombre del perfil es el de `con-name`. En esta Pi quedó como **`Tec-wifi`**; usar siempre el nombre que muestre `nmcli connection show`.
- Este script **no corta el SSH**, porque solo guarda el perfil.

Verificar:

```bash
nmcli connection show Tec-wifi | grep -E "802-1x.(eap|identity|anonymous)"
```

### Paso 6: Cambiar de red con temporizador de seguridad

La primera vez conviene probar con regreso automático al hotspot:

```bash
sudo nohup sh -c 'nmcli connection up Tec-wifi; sleep 120; nmcli connection up "netplan-wlan0-Galaxy S26 Ultra 7FE1"' >/dev/null 2>&1 &
```

Detalles:

- Las comillas dobles van **alrededor del nombre completo** del perfil (tiene espacios). Un error inicial fue escribir `Galaxy "S26 Ultra 7FE1"`, que parte el nombre en dos argumentos.
- La sesión SSH se congela. Es normal: la Pi dejó el hotspot.
- Para cancelar el regreso antes de los 120 s (si aún tienes acceso): `sudo pkill -f "sleep 120"`.
- Si apagas el hotspot, el comando de regreso falla y la Pi **se queda en `Tec`**, que es lo deseado.

### Paso 7: Cerrar la sesión SSH congelada

La conexión TCP murió pero la laptop no lo sabe. `Ctrl+D` no sirve. Usar la secuencia de escape de SSH:

1. `Enter`
2. `~`
3. `.`

O, en tmux: `Ctrl+B` luego `x` para cerrar el pane.

### Paso 8: Verificar desde la Pi (o por Tailscale)

```bash
hostname -I
nmcli connection show --active
```

Resultado obtenido:

```
10.43.50.144 100.74.216.50 fd7a:115c:a1e0::1c49:d833

Tec-wifi    ...  wifi      wlan0
tailscale0  ...  tun       tailscale0
lo          ...  loopback  lo
```

- `10.43.50.144` → IP de la red empresarial (en `wlan0`).
- `100.74.216.50` → IP de Tailscale de la Pi (fija).
- `fd7a:...` → IPv6 de Tailscale (ignorar).

### Paso 9: Entrar desde la laptop por Tailscale

```bash
ssh jorge@100.74.216.50
```

La laptop puede estar en **cualquier red** con internet, siempre que Tailscale esté activo.

### Paso 10: Dejarlo permanente

```bash
sudo nmcli connection modify Tec-wifi connection.autoconnect yes connection.autoconnect-priority 10
systemctl is-enabled tailscaled
```

Tras cada reinicio, la Pi se conectará a `Tec` y será accesible en `100.74.216.50`.

---

## 5. Cómo funciona el fallback (si `Tec` falla)

- **Falla la autenticación o la red está fuera de alcance:** NetworkManager marca el perfil como fallido y vuelve al siguiente disponible (el hotspot). Entrar de nuevo con `ssh jorge@10.53.109.82` (con el hotspot encendido).
- **Conecta pero no sirve** (portal cautivo, MAC sin registrar, aislamiento de clientes): NM lo considera exitoso y **no regresa**. Ahí Tailscale es el seguro, siempre que la red tenga salida a internet.
- **Último recurso:** monitor y teclado en la Pi, o editar la configuración en la SD desde la laptop.

Para probar sin riesgo de que un reinicio te mande a `Tec` por sorpresa, crear el perfil con `autoconnect no` y activarlo solo cuando esté confirmado.

---

## 6. Diagnóstico rápido

| Síntoma | Qué revisar |
|---|---|
| `Connection refused` | Hay respuesta pero SSH no está habilitado, o hostname/IP equivocados (`.localhost`). |
| `No route to host` | ARP sin respuesta: Pi aún arrancando, IP incorrecta, otra subred o aislamiento de clientes. |
| La Pi aparece en el teléfono pero no responde | "Aislar clientes" en el hotspot; probar banda 2.4 GHz. |
| `.local` no resuelve en Arch | Instalar `avahi` y `nss-mdns`, habilitar `avahi-daemon` y editar `hosts:` en `/etc/nsswitch.conf`. O usar la IP. |
| No sabes la IP nueva de la Pi | Usar Tailscale (`tailscale status` en la laptop) o `hostname -I` en la Pi. |
| Falla la autenticación en `Tec` | Revisar logs: `journalctl -u NetworkManager --since "15 minutes ago" \| grep -iE "tec\|auth\|fail\|activated"` |
| Tailscale no conecta en la red empresarial | La red puede bloquear salida a internet o el tráfico de Tailscale. |
| Perfil no encontrado | Nombre exacto con `nmcli connection show`. |

Comandos útiles:

```bash
nmcli connection show                 # todos los perfiles
nmcli connection show --active        # perfiles activos
nmcli device status                   # estado de las interfaces
ip -4 addr show wlan0                 # IP actual
tailscale status                      # dispositivos de Tailscale
```

---

## 7. Seguridad

- `nmcli` guarda la contraseña en **texto plano** en `/etc/NetworkManager/system-connections/` (solo root puede leerla).
- Si dejas la contraseña en `wifi.sh`, protégelo con `chmod 700` y **bórralo o limpia la contraseña** al terminar. No lo subas a GitHub.
- Pasar la contraseña como argumento (`./script.sh 'pass'`) la deja en el historial de la shell.
- No añadas tu usuario al grupo `disk` como solución permanente: da acceso a los discos casi como root.
- Sin certificado CA, la Pi no verifica la identidad del servidor de la red. Es lo que la red del Tec indica, pero implica menos protección contra redes falsas.

---

## 8. Resumen de datos de esta instalación

| Elemento | Valor |
|---|---|
| Laptop | `envy` (Arch Linux) |
| Pi | hostname `raspberrio`, usuario `jorge` |
| Perfil del hotspot | `netplan-wlan0-Galaxy S26 Ultra 7FE1` |
| Perfil empresarial | `Tec-wifi` |
| IP en hotspot | `10.53.109.82` |
| IP en `Tec` | `10.43.50.144` (puede cambiar) |
| IP de Tailscale | `100.74.216.50` (fija) |
| Entrar a la Pi | `ssh jorge@100.74.216.50` |

---

## 9. Receta corta (para repetir en otra Pi)

1. Flashear con Imager (WiFi del hotspot, SSH activado, país configurado).
2. `ssh usuario@IP_hotspot`
3. Instalar Tailscale en la Pi y en la laptop; verificar con `tailscale status`.
4. Correr el script de `nmcli` con el SSID, usuario y contraseña.
5. Activar con el temporizador de seguridad (o `sudo reboot`).
6. Reconectar con `ssh usuario@IP_tailscale`.
7. Dejar `autoconnect yes` y borrar la contraseña del script.
