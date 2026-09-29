select
    country_code,
    count(*) as regions
from {{ ref('stg_regions') }}
group by country_code
