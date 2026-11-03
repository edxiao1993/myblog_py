-- Run once on an existing MySQL/MariaDB database before deploying the routes.
-- Preserve legacy rows without guessing their owners; NULL owners remain hidden.
ALTER TABLE secret_moment
    ADD COLUMN user_id INT NULL,
    ADD INDEX ix_secret_moment_user_id (user_id),
    ADD CONSTRAINT fk_secret_moment_user_id
        FOREIGN KEY (user_id) REFERENCES user (id);
