# Grower Web Map Examples

## Prerequisites

The data pipeline must have been run for at least one farm under the grower:

```bash
export DATA_PIPELINE_DATA_ROOT=/home/coder/my-farm-advisor-runtime
```

## Generate a grower map

```bash
# Using the standalone script:
python ../../../../data-pipeline/src/scripts/reporting/generate_grower_web_map.py \
  --grower-slug plwr

# Using the Python API:
python -c "
from grower_web_map import GrowerWebMapSkill
skill = GrowerWebMapSkill(data_root='/home/coder/my-farm-advisor-runtime')
output = skill.build_grower_map(grower_slug='plwr')
print(f'Map: {output}')
"
```

## View the output

Open the generated HTML file in any browser:

```bash
open ~/my-farm-advisor-runtime/data-pipeline/growers/plwr/derived/reports/plwr_grower_map.html
```
