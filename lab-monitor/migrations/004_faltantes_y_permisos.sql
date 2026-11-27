-- 004: shared ownership, missing PPE per person, ESP32 control as a view.
--
-- Run as a PostgreSQL superuser, after schema.sql and migrations 002-003:
--     psql lab_monitor -f migrations/004_faltantes_y_permisos.sql
-- Safe to run more than once.
--
-- What it fixes / adds:
--   1. Every object is owned by the role "lab_monitor" and the team are
--      members of it. Before, tables had different owners, so changing
--      "estado" failed with "permission denied for table control_esp32"
--      (the trigger runs as whoever makes the change).
--   2. "incidencias": one row per alert from the detector, saying exactly
--      what the person was missing (safety glasses, lab coat or both).
--   3. "estado_laboratorio" gets live counts of people missing each item.
--   4. "control_esp32" becomes a view computed from "estado": same columns,
--      so app.py does not change, but one copy and one trigger less.
--
-- Only safety glasses ("lentes") and lab coat ("bata") are checked.
--
-- New objects must also belong to lab_monitor: start later migrations with
--     SET ROLE lab_monitor;

BEGIN;

-- 1. Shared owner role ---------------------------------------------------

DO $$
DECLARE
    miembro TEXT;
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'lab_monitor') THEN
        CREATE ROLE lab_monitor NOLOGIN;
    END IF;

    FOREACH miembro IN ARRAY ARRAY['jorge', 'saury', 'isra'] LOOP
        IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = miembro) THEN
            EXECUTE format('GRANT lab_monitor TO %I', miembro);
        END IF;
    END LOOP;
END
$$;

GRANT USAGE, CREATE ON SCHEMA public TO lab_monitor;

ALTER TABLE public.personas            OWNER TO lab_monitor;
ALTER TABLE public.accesos             OWNER TO lab_monitor;
ALTER TABLE public.reportes            OWNER TO lab_monitor;
ALTER TABLE public.estado              OWNER TO lab_monitor;
ALTER TABLE public.estado_laboratorio  OWNER TO lab_monitor;

-- 2. Remove the old trigger-maintained copies ----------------------------
-- (as superuser: they may belong to another member)

DROP TRIGGER IF EXISTS trigger_control_esp32 ON public.estado;
DROP FUNCTION IF EXISTS public.actualizar_control_esp32();
DROP TRIGGER IF EXISTS trigger_modo_alerta ON public.estado;
DROP FUNCTION IF EXISTS public.sincronizar_modo_alerta();

DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM pg_class
        WHERE oid = to_regclass('public.control_esp32') AND relkind = 'r'
    ) THEN
        DROP TABLE public.control_esp32;
    END IF;
END
$$;

SET ROLE lab_monitor;  -- everything below is created as lab_monitor

-- 3. ESP32 control: a view instead of a trigger-maintained copy ---------

-- Alert mode only makes sense while the system is on.
UPDATE public.estado SET alerta = FALSE WHERE NOT activo AND alerta;

ALTER TABLE public.estado
    ADD COLUMN IF NOT EXISTS actualizado TIMESTAMPTZ NOT NULL DEFAULT now();

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'alerta_requiere_activo'
          AND conrelid = 'public.estado'::regclass
    ) THEN
        ALTER TABLE public.estado
            ADD CONSTRAINT alerta_requiere_activo CHECK (activo OR NOT alerta);
    END IF;
END
$$;

CREATE OR REPLACE FUNCTION public.marcar_estado_actualizado()
RETURNS TRIGGER AS $$
BEGIN
    NEW.actualizado := now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trigger_estado_actualizado ON public.estado;
CREATE TRIGGER trigger_estado_actualizado
BEFORE UPDATE ON public.estado
FOR EACH ROW
EXECUTE FUNCTION public.marcar_estado_actualizado();

CREATE OR REPLACE VIEW public.control_esp32 AS
SELECT
    id,
    activo AND NOT alerta AS led_verde,
    activo AND alerta     AS led_rojo,
    activo AND alerta     AS buzzer,
    CASE
        WHEN NOT activo THEN 'APAGADO'
        WHEN alerta     THEN 'ALERTA'
        ELSE 'NORMAL'
    END AS modo,
    actualizado
FROM public.estado;

-- The modo_alerta copy in estado_laboratorio is kept (app.py reads it);
-- its trigger function is recreated so it belongs to lab_monitor.

CREATE FUNCTION public.sincronizar_modo_alerta()
RETURNS TRIGGER AS $$
BEGIN
    UPDATE public.estado_laboratorio
    SET modo_alerta = NEW.activo AND NEW.alerta
    WHERE id = 1;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trigger_modo_alerta
AFTER INSERT OR UPDATE ON public.estado
FOR EACH ROW
EXECUTE FUNCTION public.sincronizar_modo_alerta();

-- 4. Live counts: who is missing what right now -------------------------

ALTER TABLE public.estado_laboratorio
    ADD COLUMN IF NOT EXISTS sin_lentes INTEGER NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS sin_bata   INTEGER NOT NULL DEFAULT 0;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'faltantes_validos'
          AND conrelid = 'public.estado_laboratorio'::regclass
    ) THEN
        ALTER TABLE public.estado_laboratorio
            ADD CONSTRAINT faltantes_validos CHECK (
                sin_lentes BETWEEN 0 AND cantidad_personas
                AND sin_bata BETWEEN 0 AND cantidad_personas
            );
    END IF;
END
$$;

COMMENT ON COLUMN public.estado_laboratorio.sin_lentes IS
    'People in view right now without safety glasses';
COMMENT ON COLUMN public.estado_laboratorio.sin_bata IS
    'People in view right now without a lab coat';

-- 5. Incidents: what each person was missing ----------------------------

CREATE TABLE IF NOT EXISTS public.incidencias (
    id           SERIAL PRIMARY KEY,
    creado       TIMESTAMPTZ NOT NULL DEFAULT now(),
    track_id     INTEGER NOT NULL,          -- detector's person number (restarts with the detector)
    falta_lentes BOOLEAN NOT NULL,
    falta_bata   BOOLEAN NOT NULL,
    faltante     TEXT GENERATED ALWAYS AS (
        CASE
            WHEN falta_lentes AND falta_bata THEN 'lentes de seguridad y bata'
            WHEN falta_lentes                THEN 'lentes de seguridad'
            ELSE 'bata'
        END
    ) STORED,                               -- ready-to-show text for the dashboard
    foto         TEXT,                      -- path of the annotated photo on the Pi
    persona_id   INTEGER REFERENCES public.personas(id),  -- only if someone is identified
    CONSTRAINT algo_falta CHECK (falta_lentes OR falta_bata)
);

CREATE INDEX IF NOT EXISTS incidencias_creado_idx
    ON public.incidencias (creado DESC);

COMMENT ON TABLE public.incidencias IS
    'One row per PPE alert from the detector: what the person was missing';

RESET ROLE;
COMMIT;
