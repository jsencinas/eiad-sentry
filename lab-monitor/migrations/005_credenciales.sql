-- 005: students identified by the matrícula on their credential.
--
-- Run after 004, as a member of lab_monitor (or a superuser):
--     psql lab_monitor -f migrations/005_credenciales.sql
-- Safe to run more than once.
--
-- - personas.matricula: the student ID read from the credential (A0/L0 + 7
--   digits). Unique, so the detector can find or create the student by it.
--   Existing rows keep it empty.
-- - estado_laboratorio.sin_credencial: people in view who still have to
--   show their credential (ppe_detector.py --credential).
-- accesos (entry/exit) and incidencias.persona_id already exist.

BEGIN;
SET ROLE lab_monitor;

ALTER TABLE public.personas
    ADD COLUMN IF NOT EXISTS matricula TEXT;

CREATE UNIQUE INDEX IF NOT EXISTS personas_matricula_key
    ON public.personas (matricula);

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'matricula_valida'
          AND conrelid = 'public.personas'::regclass
    ) THEN
        ALTER TABLE public.personas
            ADD CONSTRAINT matricula_valida CHECK (matricula ~ '^[AL]0[0-9]{7}$');
    END IF;
END
$$;

ALTER TABLE public.estado_laboratorio
    ADD COLUMN IF NOT EXISTS sin_credencial INTEGER NOT NULL DEFAULT 0;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'sin_credencial_valida'
          AND conrelid = 'public.estado_laboratorio'::regclass
    ) THEN
        ALTER TABLE public.estado_laboratorio
            ADD CONSTRAINT sin_credencial_valida CHECK (sin_credencial BETWEEN 0 AND cantidad_personas);
    END IF;
END
$$;

COMMENT ON COLUMN public.personas.matricula IS
    'Student ID read from the credential (A0/L0 + 7 digits)';
COMMENT ON COLUMN public.estado_laboratorio.sin_credencial IS
    'People in view who have not shown their credential yet';

RESET ROLE;
COMMIT;
