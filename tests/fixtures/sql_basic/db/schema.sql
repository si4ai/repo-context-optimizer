-- mysql flavor: tables, keys, a function and a procedure.

-- one row per account.
-- second line of the block is not the doc.
CREATE TABLE IF NOT EXISTS Users (
  id          BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  -- login handle, unique per account.
  handle      VARCHAR(64) CHARACTER SET utf8mb4 NOT NULL,
  email       varchar(255)    null default NULL COMMENT 'contact address',
  kind        ENUM('admin','member') NOT NULL DEFAULT 'member',
  created     DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
  score       DECIMAL(10, 2) NOT NULL DEFAULT -1,  -- running score
  `Mixed Case` INT NULL,
  PRIMARY KEY (id),
  UNIQUE KEY idx_handle (handle),
  UNIQUE (email),
  KEY idx_kind_created (kind, created DESC),
  CONSTRAINT fk_users_kind FOREIGN KEY (kind) REFERENCES kinds (name) ON DELETE RESTRICT,
  CHECK (score >= -1)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='accounts that can sign in';

CREATE TEMPORARY TABLE scratch (x INT);

DROP FUNCTION IF EXISTS user_count;

DELIMITER //

-- how many users of a kind.
CREATE DEFINER=`app`@`localhost` FUNCTION user_count(p_kind VARCHAR(16)) RETURNS INT UNSIGNED
  READS SQL DATA
BEGIN
  DECLARE n INT;
  SELECT COUNT(*) INTO n FROM users WHERE kind = p_kind;
  RETURN n;
END //

CREATE PROCEDURE rename_user(
  IN  p_id     BIGINT UNSIGNED,
  IN  p_handle VARCHAR(64),
  OUT p_ok     TINYINT
)
  COMMENT 'rename one user; p_ok is 1 on success'
BEGIN
  CREATE TEMPORARY TABLE tmp_ids (id BIGINT);
  UPDATE users SET handle = p_handle WHERE id = p_id;
  SET p_ok = ROW_COUNT();
END //

DELIMITER ;
