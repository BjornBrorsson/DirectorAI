# DirectorCut-AI — Schema Index

> Maps runtime JSON schemas and file formats to their definitions.

| Schema | File | Description |
|---|---|---|
| Narrative Node Output | `narrative_node.schema.json` | Pass 1 output: spoken lines mapped to timestamps with thematic tags. |
| Visual State Event Output | `visual_event.schema.json` | Pass 2 output: gameplay state events from visual proxy. |
| Assembly Blueprint | `assembly_blueprint.schema.json` | Pass 3 output: final cut list for FCPXML generation. |
| FCPXML Template | `fcpxml_template.xml` | Reference FCPXML 1.8 sequence skeleton. |

*Schemas will be added as each pass is implemented.*
