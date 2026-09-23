# Third-party notices

## Original project code

No license grant has been selected for the original project code. Do not interpret public
availability as permission to reuse, modify or distribute it. A project license remains an
owner decision. The third-party licenses below apply to their respective components only.

## Three.js — MIT

Source: https://github.com/mrdoob/three.js

The bundled browser modules are in `static/vendor/three/`. Copyright and the complete MIT
license are preserved verbatim in `static/vendor/three/LICENSE`.

## urdf-loader — Apache License 2.0

Source: https://github.com/gkjohnson/urdf-loaders

The bundled loader is in `static/vendor/urdf-loader/`. The complete Apache License 2.0 is
preserved verbatim in `static/vendor/urdf-loader/LICENSE`; source notices are retained.

## Universal Robots ROS2 Description / UR10 assets — BSD-3-Clause

Source: https://github.com/UniversalRobots/Universal_Robots_ROS2_Description

The included UR10 configuration and visual meshes originate from this upstream project.
Its `README.md` identifies special graphical-documentation terms for UR8LONG, UR15, UR18,
UR20 and UR30 meshes, and states that all other content is BSD-3-Clause. Those five model
families are not included in this export. The upstream README and complete supplied LICENSE
are retained in `vendor/Universal_Robots_ROS2_Description/`.

This attribution also covers the copied UR10 visual meshes under `static/meshes/ur10/` and
the UR10 model data used to generate `static/ur10.urdf`. The upstream BSD license is copied
beside the static meshes as `static/meshes/LICENSE`. Preserve these notices when redistributing
assets separately. Local authoring/contributor and creation/modification metadata were removed
from the DAE asset headers only; geometry, units and axes are unchanged.

## RTDE Python client — external dependency

`requirements.txt` pins Universal Robots' RTDE Python Client Library by upstream commit.
Its source is not redistributed in this export. Review the license supplied by that dependency
when installing or redistributing it; the original-project license does not replace it.
