CREATE TABLE IF NOT EXISTS personas (
  id      SERIAL PRIMARY KEY,
  nombre  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS accesos (
  id          SERIAL PRIMARY KEY,
  persona_id  INT NOT NULL REFERENCES personas(id),
  entrada     TIMESTAMPTZ NOT NULL DEFAULT now(),
  salida      TIMESTAMPTZ            -- NULL = sigue dentro del laboratorio
);

CREATE TABLE IF NOT EXISTS reportes (
  id           SERIAL PRIMARY KEY,
  descripcion  TEXT NOT NULL,
  creado       TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Una sola fila con el estado del sistema
CREATE TABLE IF NOT EXISTS estado (
  id      INT PRIMARY KEY DEFAULT 1 CHECK (id = 1),
  activo  BOOLEAN NOT NULL DEFAULT FALSE,
  alerta  BOOLEAN NOT NULL DEFAULT FALSE
);
INSERT INTO estado (id) VALUES (1) ON CONFLICT DO NOTHING;

-- Datos de ejemplo (opcional)
INSERT INTO personas (nombre) VALUES ('Ana López'), ('Carlos Ruiz'), ('Luis Pérez');
INSERT INTO accesos (persona_id, entrada, salida) VALUES
  (1, now() - interval '2 hours', NULL),
  (2, now() - interval '1 hour', NULL),
  (3, now() - interval '5 hours', now() - interval '4 hours');
INSERT INTO reportes (descripcion) VALUES ('Persona no registrada detectada');
