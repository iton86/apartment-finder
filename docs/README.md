# Project documentation

This documentation describes the implementation in this repository. It separates
current behavior from planned functionality and provides instructions for
contributors and operators. Commands assume the repository root unless stated
otherwise; placeholders such as `<search-url>` must be replaced.

## Reading paths

| Task | Read |
|---|---|
| Run the project locally | [Development guide](development.md) |
| Understand components and stored data | [Architecture](architecture.md) |
| Design SofiaPlan boundary and POI ingestion (proposed) | [Geospatial architecture](sofiaplan-geospatial-architecture.md) |
| Find official street polygons and address exports | [GIS-Sofia open data](gis-sofia-open-data.md) |
| Import reference geometry and locate coordinates | [Geospatial operations](geospatial-operations.md) |
| View planning units and accepted ad assignments | [Local map](planning-unit-map.md), [Resolution algorithm](planning-unit-resolution.md) |
| Resolve ads, review units and train calibration | [Resolver operations](location-resolver-operations.md) |
| Configure a process | [Configuration reference](configuration.md) |
| Run containers or recurring scrapes | [Operations guide](operations.md) |
| Prepare labels and train an encoder | [Machine-learning overview](machine-learning.md), then [training guide](../training/README.md) |

## Documentation maintenance

Update the relevant document when changing a command, configuration variable,
database behavior, or integration boundary. Keep examples consistent with the
CLI parsers, Makefile, Dockerfile, and migrations. Describe incomplete features
as incomplete rather than presenting the target architecture as implemented.

The [root README](../README.md) is the project overview. The detailed
[training guide](../training/README.md) remains the annotation workflow reference.
Cloud portal screens, service quotas, and provider prices are not versioned by
this repository; verify them with the provider when provisioning resources.
