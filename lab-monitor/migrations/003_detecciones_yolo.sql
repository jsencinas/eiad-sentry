
-- Metadatos para detecciones de YOLO
-- No modifica los booleanos ni los triggers existentes.

BEGIN;

ALTER TABLE public.estado_laboratorio
    ADD COLUMN IF NOT EXISTS deteccion_estado TEXT
        NOT NULL DEFAULT 'SIN_SENAL',
    ADD COLUMN IF NOT EXISTS ultima_deteccion TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS cantidad_personas INTEGER
        NOT NULL DEFAULT 0;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'estado_deteccion_valida'
          AND conrelid = 'public.estado_laboratorio'::regclass
    ) THEN
        ALTER TABLE public.estado_laboratorio
        ADD CONSTRAINT estado_deteccion_valida
        CHECK (
            deteccion_estado IN (
                'CONFIRMADA',
                'PENDIENTE',
                'SIN_SENAL'
            )
        );
    END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'cantidad_personas_valida'
          AND conrelid = 'public.estado_laboratorio'::regclass
    ) THEN
        ALTER TABLE public.estado_laboratorio
        ADD CONSTRAINT cantidad_personas_valida
        CHECK (cantidad_personas >= 0);
    END IF;
END
$$;

COMMIT;
