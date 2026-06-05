CREATE TABLE IF NOT EXISTS locations (
    id          SERIAL PRIMARY KEY,
    name        VARCHAR(100) NOT NULL,
    latitude    DECIMAL(9,6),
    longitude   DECIMAL(9,6),
    description TEXT,
    created_at  TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS devices (
    id          SERIAL PRIMARY KEY,
    device_uid  VARCHAR(64) UNIQUE NOT NULL,
    name        VARCHAR(100) NOT NULL,
    device_type VARCHAR(50)  NOT NULL,
    location_id INTEGER REFERENCES locations(id),

    voltage_min DECIMAL(8,2),
    voltage_max DECIMAL(8,2),
    current_min DECIMAL(8,2),
    current_max DECIMAL(8,2),
    temp_min    DECIMAL(5,2),
    temp_max    DECIMAL(5,2),
    is_active   BOOLEAN DEFAULT TRUE,
    created_at  TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS event_log (
    id              SERIAL PRIMARY KEY,
    event_type      VARCHAR(50) NOT NULL,
    severity        VARCHAR(20) NOT NULL,
    device_id       INTEGER REFERENCES devices(id),
    description     TEXT NOT NULL,
    llm_explanation TEXT,
    command_json    JSONB,
    command_ack     BOOLEAN DEFAULT FALSE,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);


CREATE TABLE IF NOT EXISTS device_schemas (
    device_type  VARCHAR(50) PRIMARY KEY,
    description  TEXT,
    version      INT DEFAULT 1,
    fields       JSONB NOT NULL,
    updated_at   TIMESTAMPTZ DEFAULT NOW()
);

INSERT INTO device_schemas (device_type, description, fields) VALUES

('inverter', 'Мережевий / автономний інвертор', '[
  {"name":"dc_voltage_v",      "required":true,  "min":40.0,  "max":60.0,  "unit":"V",  "description":"Напруга шини постійного струму"},
  {"name":"dc_current_a",      "required":false, "min":-100.0,"max":100.0, "unit":"A",  "description":"Струм шини постійного струму"},
  {"name":"ac_output_power_w", "required":true,  "min":0.0,   "max":3500.0,"unit":"W",  "description":"Вихідна потужність змінного струму"},
  {"name":"ac_output_v",       "required":false, "min":195.0, "max":245.0, "unit":"V",  "description":"Вихідна напруга змінного струму"},
  {"name":"temperature_c",     "required":true,  "min":-10.0, "max":60.0,  "unit":"°C", "description":"Температура радіатора"}
]'::jsonb),

('charge_controller', 'Сонячний контролер заряду MPPT', '[
  {"name":"pv_power_w",        "required":true,  "min":0.0,   "max":3000.0,"unit":"W",  "description":"Потужність фотоелектричних панелей"},
  {"name":"pv_voltage_v",      "required":false, "min":0.0,   "max":400.0, "unit":"V",  "description":"Напруга панелей"},
  {"name":"battery_voltage_v", "required":true,  "min":40.0,  "max":60.0,  "unit":"V",  "description":"Напруга батареї"},
  {"name":"charge_current_a",  "required":false, "min":0.0,   "max":100.0, "unit":"A",  "description":"Струм заряду"},
  {"name":"temperature_c",     "required":true,  "min":-10.0, "max":55.0,  "unit":"°C", "description":"Температура контролера"}
]'::jsonb),

('battery', 'Акумуляторна батарея LiFePO4', '[
  {"name":"soc_pct",       "required":true,  "min":0.0,   "max":100.0, "unit":"%",  "description":"Рівень заряду (State of Charge)"},
  {"name":"voltage_v",     "required":true,  "min":44.0,  "max":58.4,  "unit":"V",  "description":"Напруга батареї"},
  {"name":"current_a",     "required":true,  "min":-50.0, "max":50.0,  "unit":"A",  "description":"Струм (+ заряд, - розряд)"},
  {"name":"temperature_c", "required":true,  "min":-10.0, "max":45.0,  "unit":"°C", "description":"Температура елементів"},
  {"name":"health_pct",    "required":false, "min":0.0,   "max":100.0, "unit":"%",  "description":"State of Health батареї"}
]'::jsonb),

('sensor', 'Датчик навколишнього середовища', '[
  {"name":"temperature_c", "required":true,  "min":-20.0, "max":50.0,  "unit":"°C", "description":"Температура приміщення"},
  {"name":"humidity_pct",  "required":false, "min":0.0,   "max":100.0, "unit":"%",  "description":"Відносна вологість"},
  {"name":"ac_mains_v",    "required":false, "min":190.0, "max":250.0, "unit":"V",  "description":"Напруга мережі 220В"}
]'::jsonb);

INSERT INTO locations (name, description) VALUES
    ('Об''єкт A — Офіс', 'Тестовий об''єкт для розробки');

INSERT INTO devices (device_uid, name, device_type, location_id,
                     voltage_min, voltage_max, current_min, current_max,
                     temp_min, temp_max) VALUES
    ('inverter_01',         'Інвертор Victron 3kW',    'inverter',           1, 40.0, 60.0, -100.0, 100.0, -10.0, 60.0),
    ('charge_controller_01','Контролер MPPT 100A',     'charge_controller',  1, 40.0, 60.0,    0.0, 100.0, -10.0, 55.0),
    ('battery_01',          'АКБ LiFePO4 200Ah',       'battery',            1, 44.0, 58.4,  -50.0,  50.0, -10.0, 45.0),
    ('battery_02',          'АКБ LiFePO4 200Ah #2',   'battery',            1, 44.0, 58.4,  -50.0,  50.0, -10.0, 45.0),
    ('sensor_env_01',       'Датчик середовища (кімн.)','sensor',            1,   NULL, NULL,  NULL,  NULL, -20.0, 50.0);
