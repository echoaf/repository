-- 注意truncate命令只在新安装mysql时候使用

TRUNCATE TABLE mysql.user;

GRANT ALL PRIVILEGES ON *.* TO 'root'@'localhost' IDENTIFIED BY 'redhat' WITH GRANT OPTION;
GRANT SELECT, INSERT, UPDATE, DELETE, CREATE, DROP, RELOAD, SHUTDOWN, PROCESS, FILE, REFERENCES, INDEX, ALTER, SHOW DATABASES, CREATE TEMPORARY TABLES, LOCK TABLES, EXECUTE, REPLICATION SLAVE, REPLICATION CLIENT, CREATE VIEW, SHOW VIEW, CREATE ROUTINE, ALTER ROUTINE, CREATE USER, EVENT, TRIGGER, CREATE TABLESPACE ON *.* TO 'admin'@'localhost' IDENTIFIED BY 'redhat';
GRANT ALL ON *.* TO 'admin_user'@'%' IDENTIFIED BY 'redhat' WITH GRANT OPTION;

GRANT SELECT, RELOAD, REPLICATION SLAVE, REPLICATION CLIENT, PROCESS ON *.* TO 'dump_user'@'%' IDENTIFIED BY 'redhat';
GRANT SELECT ON *.* TO 'read_user'@'%' IDENTIFIED BY 'redhat';
GRANT SELECT, REPLICATION SLAVE, REPLICATION CLIENT ON *.* TO 'repl_user'@'%' IDENTIFIED BY 'redhat';

FLUSH PRIVILEGES;