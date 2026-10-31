/* ============================================================================
   Prechequeo de la tabla fuente del D10 antes de importar (sólo lectura).

   Lo ejecuta una persona autorizada en SSMS / Azure Data Studio, en el PC con
   acceso a la base. Todas las consultas devuelven AGREGADOS (conteos, tipos de
   columna, tamaños): ninguna devuelve RUT ni filas individuales.

   Ajuste el nombre de la tabla si no es cmfdat.dbo.D10C.
   ============================================================================ */

USE cmfdat;
SET NOCOUNT ON;
SET TRANSACTION ISOLATION LEVEL READ UNCOMMITTED;  -- evita bloquear cargas en curso

/* 1. Tipos de columna: definen formato.periodo y formato.rut en config/d10.yaml */
SELECT COLUMN_NAME, DATA_TYPE, CHARACTER_MAXIMUM_LENGTH, NUMERIC_PRECISION, IS_NULLABLE
FROM INFORMATION_SCHEMA.COLUMNS
WHERE TABLE_SCHEMA = 'dbo' AND TABLE_NAME = 'D10C'
ORDER BY ORDINAL_POSITION;

/* 2. Tamaño de la tabla: dimensiona disco y tiempo de importación */
EXEC sp_spaceused 'dbo.D10C';

/* 3. Índices: la importación filtra por periodo (WHERE periodo = ?).
      Sin un índice que empiece por periodo, cada periodo es un scan completo. */
SELECT i.name AS indice, i.type_desc, c.name AS columna, ic.key_ordinal
FROM sys.indexes i
JOIN sys.index_columns ic ON ic.object_id = i.object_id AND ic.index_id = i.index_id
JOIN sys.columns c ON c.object_id = ic.object_id AND c.column_id = ic.column_id
WHERE i.object_id = OBJECT_ID('dbo.D10C')
ORDER BY i.name, ic.key_ordinal;

/* 4. Periodos presentes: cuáles son cierres de mes y cuántas filas tiene cada uno.
      Si periodo no es DATE/DATETIME, ajuste la conversión (ver comentarios). */
SELECT
    periodo,
    COUNT_BIG(*)               AS filas,
    COUNT(DISTINCT rut_deudor) AS deudores,
    COUNT(DISTINCT rutis_sbif) AS ifs,
    SUM(CAST(monto AS DECIMAL(38, 0))) / 1e6 AS monto_MM,
    CASE WHEN CAST(periodo AS DATE) = EOMONTH(CAST(periodo AS DATE)) THEN 'cierre de mes' ELSE 'semanal' END AS tipo_corte
    -- periodo INT aaaammdd: reemplace CAST(periodo AS DATE) por CONVERT(DATE, CAST(periodo AS CHAR(8)), 112)
    -- periodo INT aaaamm:   cada valor ya es un mes; use EOMONTH(CONVERT(DATE, CAST(periodo AS CHAR(6)) + '01', 112))
FROM dbo.D10C
GROUP BY periodo
ORDER BY periodo;

/* 5. Calidad de códigos: todo lo que está fuera del manual se cuenta (el ajuste lo descarta) */
SELECT 'tipo_deudor' AS campo, CAST(tipo_deudor AS VARCHAR(10)) AS valor, COUNT_BIG(*) AS filas
FROM dbo.D10C GROUP BY tipo_deudor
UNION ALL
SELECT 'tipo_credito', CAST(tipo_credito AS VARCHAR(10)), COUNT_BIG(*) FROM dbo.D10C GROUP BY tipo_credito
UNION ALL
SELECT 'tipo_mora', CAST(tipo_mora AS VARCHAR(10)), COUNT_BIG(*) FROM dbo.D10C GROUP BY tipo_mora
ORDER BY campo, valor;

/* 6. Nulos, montos no positivos y reglas del manual */
SELECT
    COUNT_BIG(*)                                                     AS filas,
    SUM(CASE WHEN rut_deudor IS NULL THEN 1 ELSE 0 END)              AS rut_nulo,
    SUM(CASE WHEN periodo IS NULL THEN 1 ELSE 0 END)                 AS periodo_nulo,
    SUM(CASE WHEN monto IS NULL OR monto <= 0 THEN 1 ELSE 0 END)     AS monto_nulo_o_no_positivo,
    SUM(CASE WHEN tipo_credito IN (6, 7, 11) AND tipo_mora <> 0 THEN 1 ELSE 0 END) AS contingente_con_mora,
    SUM(CASE WHEN monto <> ROUND(monto, 0) THEN 1 ELSE 0 END)        AS monto_con_decimales
FROM dbo.D10C;

/* 7. Forma del RUT (sin mostrar ninguno): largo en caracteres y si trae guion o DV.
      Define formato.rut: con guion o con 'K' => con DV; sólo dígitos => revisar si el último es DV. */
SELECT
    LEN(CAST(rut_deudor AS VARCHAR(20)))                                        AS largo,
    CASE WHEN CAST(rut_deudor AS VARCHAR(20)) LIKE '%-%' THEN 1 ELSE 0 END      AS tiene_guion,
    CASE WHEN CAST(rut_deudor AS VARCHAR(20)) LIKE '%[kK]' THEN 1 ELSE 0 END    AS termina_en_k,
    COUNT_BIG(*)                                                                AS filas
FROM dbo.D10C
GROUP BY LEN(CAST(rut_deudor AS VARCHAR(20))),
         CASE WHEN CAST(rut_deudor AS VARCHAR(20)) LIKE '%-%' THEN 1 ELSE 0 END,
         CASE WHEN CAST(rut_deudor AS VARCHAR(20)) LIKE '%[kK]' THEN 1 ELSE 0 END
ORDER BY largo;

/* 8. Personas vs empresas según el umbral de RUT (formato.umbral_rut_empresa = 50.000.000).
      Ajuste la expresión si el RUT trae DV (dividir por 10 o cortar antes del guion). */
SELECT
    CASE WHEN TRY_CAST(rut_deudor AS BIGINT) >= 50000000 THEN 'empresa (>= 50M)' ELSE 'persona (< 50M)' END AS segmento,
    COUNT(DISTINCT rut_deudor) AS deudores
FROM dbo.D10C
WHERE periodo = (SELECT MAX(periodo) FROM dbo.D10C)
GROUP BY CASE WHEN TRY_CAST(rut_deudor AS BIGINT) >= 50000000 THEN 'empresa (>= 50M)' ELSE 'persona (< 50M)' END;
