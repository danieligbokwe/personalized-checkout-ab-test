CREATE TABLE users (
    user_id        VARCHAR(12) PRIMARY KEY,
    signup_date    DATE NOT NULL,
    platform       VARCHAR(10) NOT NULL CHECK (platform IN ('iOS', 'Android'))
);

CREATE TABLE experiment_assignments (
    assignment_id     VARCHAR(12) PRIMARY KEY,
    user_id           VARCHAR(12) NOT NULL REFERENCES users(user_id),
    variant           VARCHAR(10) NOT NULL CHECK (variant IN ('control', 'treatment')),
    first_assigned_ts TIMESTAMP NOT NULL,
    UNIQUE (user_id)
);

CREATE TABLE checkout_sessions (
    session_id  VARCHAR(12) PRIMARY KEY,
    user_id     VARCHAR(12) NOT NULL REFERENCES users(user_id),
    session_ts  TIMESTAMP NOT NULL,
    completed   BOOLEAN NOT NULL
);

CREATE TABLE orders (
    order_id      VARCHAR(12) PRIMARY KEY,
    session_id    VARCHAR(12) NOT NULL REFERENCES checkout_sessions(session_id),
    order_amount  DECIMAL(10,2) NOT NULL CHECK (order_amount > 0),
    unit_count    INT NOT NULL CHECK (unit_count > 0),
    order_date    DATE NOT NULL,
    UNIQUE (session_id)
);