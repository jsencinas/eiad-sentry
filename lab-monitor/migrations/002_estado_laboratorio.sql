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
