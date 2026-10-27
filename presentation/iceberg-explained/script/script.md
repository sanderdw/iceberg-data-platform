# Hoe werkt Apache Iceberg?

Script van de voice-over, per hoofdstuk. Bron: `edit/cut.json` (pas daar aan). Tussen haken staat wat je in beeld ziet.


## 01 · Een tabelformaat

- *[schema: layers]* Apache Iceberg is geen dataplatform. Het is een open-source tabelformaat voor grote datasets. Het beschrijft hoe bestanden en metadata samen één tabel vormen, zodat verschillende engines die tabel betrouwbaar kunnen lezen en schrijven.
- *[schema: layers]* De data zelf staat in bestanden, meestal Parquet, in S3-compatibele objectopslag, zoals RustFS. Daarnaast houdt Iceberg metadata bij: welke bestanden bij de tabel horen, met welk schema, en op welk moment.
- *[schema: layers]* Die metadata is een boom. Het metadata-bestand bevat het schema van de tabel en de lijst met snapshots: elke versie van de tabel. Per snapshot is er een manifest list, die zegt welke manifests bij die versie horen. En een manifest somt de Parquet-bestanden op, met per kolom de kleinste en grootste waarde. Zo kan een engine bestanden overslaan zonder ze te openen.
- *[schema: layers]* Bovenaan staat de catalogus. Dat is een technische catalogus voor Iceberg-tabellen, geen datacatalogus waarin je dataproducten zoekt. Die weet per tabel maar één ding: waar het actuele metadata-bestand staat.
- *[schema: layers]* Een client, zoals DuckDB of Spark, praat met de catalogus via de Iceberg REST API. Om te lezen vraagt hij de metadata van de tabel op. Daarmee bepaalt hij snel welke Parquet-bestanden hij nodig heeft, en alleen die haalt hij uit de bucket.
- *[schema: layers]* Om te schrijven zet de client eerst nieuwe Parquet-bestanden in de bucket. Bestaande bestanden worden nooit aangepast. Daarna vraagt hij de catalogus via de REST API om de wijziging vast te leggen. Die schrijft een nieuw metadata-bestand en verzet de verwijzing in één keer. Zo wordt elke wijziging een nieuwe snapshot.
- *[schema: layers]* Ter vergelijking: in Postgres zit dit allemaal in één server. Data en metadata zijn daar hard aan elkaar gekoppeld, en elke query wordt op diezelfde server uitgevoerd. Bij Iceberg zijn het losse onderdelen: een catalogus, een bucket en een engine naar keuze.

## 02 · De bouwstenen

- *[schema: functions]* Die losse bouwstenen zet ik op een rij, naast Postgres. Per functie zie je wat Postgres zelf doet, wat dit platform gebruikt, en wat de alternatieven zijn.
- *[schema: functions]* Opslag. Postgres schrijft naar de schijf van zijn eigen server. Bij Iceberg is dat objectopslag: hier RustFS, maar net zo goed Amazon S3 of Azure Blob Storage.
- *[schema: functions]* Bestandsformaat. Postgres bewaart zijn data in PGDATA, als binaire blobs in zijn eigen formaat. Iceberg gebruikt meestal Parquet. Dat is kolomgericht, zodat een engine alleen de kolommen leest die hij nodig heeft.
- *[schema: functions]* Tabelformaat. Wat Postgres intern regelt, legt Iceberg vast als open tabelformaat. Delta Lake en Apache Hudi doen hetzelfde, op hun eigen manier.
- *[schema: functions]* Metadata-catalogus. Postgres heeft zijn systeemcatalogus. Hier is dat Apache Polaris, dat zijn gegevens zelf weer in Postgres bewaart. Lakekeeper of AWS Glue kunnen die rol ook vervullen.
- *[schema: functions]* Catalogus-API. Met Postgres praat je via zijn eigen protocol, of via ODBC. Een Iceberg-catalogus spreekt de Iceberg REST API, dus elke client die die API kent, kan ermee werken.
- *[schema: functions]* En de query engine. Die zit in Postgres ingebouwd. Bij Iceberg kies je hem zelf: DuckDB, PyIceberg, Polars, Spark of Snowflake.
- *[schema: functions]* In Postgres zitten al die functies in één systeem. Bij Iceberg is elke functie een eigen bouwsteen, die je los kunt vervangen.
- *[schema: platform]* Om het simpel te houden, is één database hier één catalogus in Polaris, met één eigen bucket in RustFS.
- *[c2-database]* Ik maak in het portaal de database multiverse aan.
- *[c2-bucket]* En in RustFS staat direct een nieuwe, lege bucket.

## 03 · Een tabel maken

- *[c3-notebook]* De tabel maak ik in een notebook, met DuckDB. PyIceberg, Polars of Spark kunnen precies hetzelfde.
- *[c3-notebook]* DuckDB leest alle personages uit de Rick and Morty API en schrijft ze als Parquet naar de bucket. Polaris legt het nieuwe metadata-bestand vast.
- *[c3-notebook]* Elke schrijfactie is een snapshot. Deze eerste snapshot bestaat uit één Parquet-bestand.
- *[c3-portal]* In de catalogus van het portaal staat de tabel nu ook.
- *[c3-portal]* Met de locatie in de bucket: de namespace en de tabel als mappen. En het schema dat in de metadata staat.
- *[c3-portal]* Een preview van de data leest het portaal met mijn eigen rechten.

## 04 · Onder de motorkap

- *[c4-rustfs]* In RustFS zie je precies die structuur terug: per tabel een map met metadata en een map met data.
- *[c4-rustfs]* In de metadata-map staan de metadata-bestanden, de manifest list van de snapshot en het manifest.
- *[c4-rustfs]* Een metadata-bestand is gewoon JSON: het schema, de locatie en de snapshots, met per snapshot een verwijzing naar de manifest list.
- *[c4-rustfs-data]* In de data-map staat het Parquet-bestand met de personages zelf.
- *[c4-pgadmin]* Om te laten zien wat Polaris bewaart, zit pgAdmin in het platform. Polaris heeft maar een paar tabellen nodig: entities voor catalogi, namespaces en tabellen, en grant_records voor de rechten.
- *[c4-pgadmin]* De database multiverse is hier een catalogus, met de bucket als basislocatie.
- *[c4-pgadmin]* En voor de tabel bewaart Polaris alleen het pad naar het actuele metadata-bestand. Geen data, alleen een verwijzing. De rest staat in de bucket.

## 05 · Een delete

- *[c5-notebook]* Nu verwijder ik Jerry Smith uit de tabel.
- *[c5-notebook]* DuckDB herschrijft het Parquet-bestand niet. Het schrijft een klein delete-bestand dat de rijen markeert, en maakt een nieuwe snapshot.
- *[c5-notebook]* De eerste snapshot bestaat nog. Met time travel lees ik de tabel zoals die was, en dan staat Jerry er gewoon in.
- *[c5-portal]* Het portaal toont nu twee snapshots: het toevoegen en de delete.
- *[c5-portal]* En in een preview van de eerste snapshot staat Jerry er nog.
- *[c5-rustfs]* In de bucket staat het oorspronkelijke Parquet-bestand nog, met het delete-bestand ernaast.
- *[schema: deletes]* Hoe die markering eruitziet, hangt af van de versie en de engine. In formaatversie 2, zoals hier, is het een delete-bestand met de posities van de verwijderde rijen.
- *[schema: deletes]* In versie 3 is het een deletion vector: een compacte bitmap in een Puffin-bestand, één per databestand.
- *[schema: deletes]* Andere engines kiezen voor copy-on-write. Die herschrijven het databestand meteen, zonder de verwijderde rijen.
- *[schema: deletes]* Maar in alle drie de gevallen blijft het oude bestand bestaan, zolang een oudere snapshot ernaar verwijst. Dat is handig voor time travel en voor lezers die nog met een oudere snapshot werken. Maar de data is er nog.

## 06 · Echt weg

- *[schema: permanent]* Moet een delete echt weg zijn, bijvoorbeeld voor de AVG, dan zijn er drie stappen.
- *[schema: permanent]* Eén: herschrijf de data zonder de verwijderde rijen. Twee: laat de oude snapshots verlopen. Drie: ruim de bestanden op waar geen snapshot meer naar verwijst.
- *[c6-notebook]* DuckDB kan nog geen snapshots laten verlopen, dus deze stappen doe ik met PyIceberg, op dezelfde tabel. Eerst het herschrijven: een nieuw Parquet-bestand zonder Jerry.
- *[c6-notebook]* Dan laat ik alle snapshots verlopen, behalve de huidige.
- *[c6-notebook]* Verlopen haalt de snapshots alleen uit de metadata. De bestanden blijven staan. Daarom zoek ik de bestanden op waar niets meer naar verwijst, en verwijder ik die.
- *[c6-notebook]* De controle: time travel naar de eerste snapshot kan niet meer, en in geen enkel Parquet-bestand staat Jerry nog.
- *[c6-portal]* Het portaal toont nog maar één snapshot.
- *[c6-rustfs]* En in de bucket is het oorspronkelijke bestand weg.
- *[c6-rustfs]* Snowflake regelt dit zelf, met een vaste bewaartermijn. Bij Iceberg plan je dit onderhoud zelf in, met de engine die je kiest.

## 07 · Je eigen engine

- *[schema: compare]* Technisch werkt Snowflake hetzelfde. De data staat in aparte opslag, en de metadata wordt op een andere plek bijgehouden. Alleen beheert Snowflake dat volledig zelf.
- *[schema: compare]* Het verschil met Apache Iceberg is dat je bij open source je eigen engine kiest. En dat aan twee kanten.
- *[schema: engines]* Aan de kant van de producent schrijf je met het framework van je keuze naar Iceberg. Dat kan Python zijn, Spark, of zelfs Snowflake.
- *[schema: engines]* Staat de data er eenmaal, dan is die engine weer weg. De data hangt aan geen enkele engine vast.
- *[schema: engines]* Aan de kant van de consument kies je opnieuw. Ik gebruik vaak DuckDB of Polars, maar ook Snowflake kan zo'n catalogus koppelen.
- *[c7-notebook]* Als consument lees ik de tabel hier met DuckDB. De engine haalt alleen de bestanden op die hij nodig heeft, en rekent in mijn eigen omgeving.

## 08 · Uitwisselen

- *[schema: combine]* De kracht van Snowflake, of een ander geïntegreerd dataplatform, is dat alle data op hetzelfde platform staat. Data van verschillende producenten combineer je daar snel, op één groot compute-platform.
- *[schema: combine]* Met Iceberg lukt dat niet op dezelfde manier. Bieden meerdere producenten elk hun dataproduct aan via Iceberg, dan haal je als consument de data binnen die je nodig hebt.
- *[schema: combine]* Dat gaat slim, want je haalt alleen op wat je echt nodig hebt. Maar je verwerkt het in je eigen engine, dus er vindt altijd een data-overdracht plaats.
- *[schema: combine]* Je kunt Apache Iceberg zien als een mechanisme om data uit te wisselen, en niet als vervanging van een dataplatform zoals Snowflake. Ze vullen elkaar aan.

## 09 · Het dataproduct

- *[h8-om]* Zo'n Iceberg-tabel is de basis van een dataproduct. Je kunt er een semantisch model op zetten, met definities en metrics.
- *[h8-om]* En je kunt het dataproduct publiceren in een datacatalogus, zoals hier in OpenMetadata. Hoe dat werkt, laat ik zien in de end-to-end demo.

## Einde

- *[end]* Probeer het zelf. Installeer het platform met één commando, en maak je eigen Iceberg-tabel.
