select
    region_id,
    lower(region) as region,
    upper(country) as country_code
from {{ ref('regions') }}
