BEGIN;

CREATE TABLE IF NOT EXISTS personas (
    id SERIAL PRIMARY KEY,
    nombre TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS accesos (
    id SERIAL PRIMARY KEY,
    persona_id INT NOT NULL REFERENCES personas(id),
    entrada TIMESTAMPTZ NOT NULL DEFAULT now(),
    salida TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS reportes (
    id SERIAL PRIMARY KEY,
    descripcion TEXT NOT NULL,
    creado TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS estado (
    id INT PRIMARY KEY DEFAULT 1 CHECK (id = 1),
    activo BOOLEAN NOT NULL DEFAULT FALSE,
    alerta BOOLEAN NOT NULL DEFAULT FALSE
);

CREATE TABLE IF NOT EXISTS control_esp32 (
    id INT PRIMARY KEY DEFAULT 1 CHECK (id = 1),
    led_verde BOOLEAN NOT NULL DEFAULT FALSE,
    led_rojo BOOLEAN NOT NULL DEFAULT FALSE,
    buzzer BOOLEAN NOT NULL DEFAULT FALSE,
    modo TEXT NOT NULL DEFAULT 'APAGADO',
    actualizado TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE OR REPLACE FUNCTION actualizar_control_esp32()
RETURNS TRIGGER AS $$
BEGIN
    INSERT INTO control_esp32 (
        id,
        led_verde,
        led_rojo,
        buzzer,
        modo,
        actualizado
    )
    VALUES (
        1,
        NEW.activo AND NOT NEW.alerta,
        NEW.activo AND NEW.alerta,
        NEW.activo AND NEW.alerta,
        CASE
            WHEN NOT NEW.activo THEN 'APAGADO'
            WHEN NEW.alerta THEN 'ALERTA'
            ELSE 'NORMAL'
        END,
        now()
    )
    ON CONFLICT (id) DO UPDATE SET
        led_verde = EXCLUDED.led_verde,
        led_rojo = EXCLUDED.led_rojo,
        buzzer = EXCLUDED.buzzer,
        modo = EXCLUDED.modo,
        actualizado = EXCLUDED.actualizado;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trigger_control_esp32 ON estado;

CREATE TRIGGER trigger_control_esp32
AFTER INSERT OR UPDATE ON estado
FOR EACH ROW
EXECUTE FUNCTION actualizar_control_esp32();

INSERT INTO estado (id)
VALUES (1)
ON CONFLICT (id) DO NOTHING;

UPDATE estado
SET activo = activo
WHERE id = 1;

COMMIT;

-- Estado del laboratorio para la ESP32
BEGIN;

CREATE TABLE IF NOT EXISTS estado_laboratorio (
    id INT PRIMARY KEY DEFAULT 1 CHECK (id = 1),
    personas BOOLEAN NOT NULL DEFAULT FALSE,
    proteccion BOOLEAN NOT NULL DEFAULT FALSE,
    modo_alerta BOOLEAN NOT NULL DEFAULT FALSE
);

INSERT INTO estado_laboratorio (id)
VALUES (1)
ON CONFLICT (id) DO NOTHING;

COMMIT;

BEGIN;

CREATE OR REPLACE FUNCTION sincronizar_modo_alerta()
RETURNS TRIGGER AS $$
BEGIN
    UPDATE public.estado_laboratorio
    SET modo_alerta = NEW.activo AND NEW.alerta
    WHERE id = 1;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trigger_modo_alerta ON estado;

CREATE TRIGGER trigger_modo_alerta
AFTER INSERT OR UPDATE ON estado
FOR EACH ROW
EXECUTE FUNCTION sincronizar_modo_alerta();

UPDATE public.estado_laboratorio
SET modo_alerta = (
    SELECT activo AND alerta
    FROM public.estado
    WHERE id = 1
)
WHERE id = 1;

COMMIT;
