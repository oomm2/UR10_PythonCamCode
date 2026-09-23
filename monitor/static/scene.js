import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import URDFLoader from './vendor/urdf-loader/src/URDFLoader.js';
import { prepareRobot, applyJointValues, JOINT_NAMES, decimateTrack } from './telemetry.js';

const DEFAULT_CAMERA = { position: [1.9, 1.35, 2.0], target: [0, 0, 0.55] };
const SCENE_BACKGROUNDS = { dark: 0x111827, light: 0xe5e7eb };
const GRID_COLOURS = { dark: [0x334155, 0x1e293b], light: [0xcbd5e1, 0x94a3b8] };

/**
 * Build the 3D digital twin plus the optional overlays: the recorded TCP trail,
 * per-joint limit rings and single-joint highlighting.
 *
 * The overlay helpers never read from the network; the caller feeds them data.
 * The scene stays read-only with respect to the robot: nothing here can issue a
 * command.
 */
export function createRobotScene(element, status, log, { robotModel = 'UR10', urdfPath = null } = {}) {
  const scene = new THREE.Scene();
  scene.background = new THREE.Color(SCENE_BACKGROUNDS.dark);
  const camera = new THREE.PerspectiveCamera(40, 1, 0.01, 20);
  camera.position.set(...DEFAULT_CAMERA.position);
  const renderer = new THREE.WebGLRenderer({ antialias: true, preserveDrawingBuffer: true });
  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
  renderer.outputColorSpace = THREE.SRGBColorSpace;
  element.append(renderer.domElement);
  const controls = new OrbitControls(camera, renderer.domElement);
  controls.target.set(...DEFAULT_CAMERA.target);
  controls.enableDamping = true;
  controls.minDistance = 0.8;
  controls.maxDistance = 4.5;
  scene.add(new THREE.HemisphereLight(0xc7d2fe, 0x1e293b, 2.4));
  const key = new THREE.DirectionalLight(0xffffff, 3.5);
  key.position.set(2, 3, 4);
  scene.add(key);
  let grid = new THREE.GridHelper(2.5, 25, ...GRID_COLOURS.dark);
  scene.add(grid);
  const axes = new THREE.AxesHelper(0.35);
  axes.visible = false;
  scene.add(axes);

  // --- Recorded TCP trail ------------------------------------------------
  // A single preallocated line that grows in place: reallocating a BufferGeometry
  // every frame would thrash the GC during a long capture.
  const TRAIL_CAPACITY = 8000;
  const trailPositions = new Float32Array(TRAIL_CAPACITY * 3);
  const trailGeometry = new THREE.BufferGeometry();
  trailGeometry.setAttribute('position', new THREE.BufferAttribute(trailPositions, 3));
  trailGeometry.setDrawRange(0, 0);
  const trailMaterial = new THREE.LineBasicMaterial({ color: 0xf97316, transparent: true, opacity: 0.9 });
  const trailLine = new THREE.Line(trailGeometry, trailMaterial);
  trailLine.frustumCulled = false;
  trailLine.visible = false;
  scene.add(trailLine);
  let trailCount = 0;

  // A small sphere marks the current position along a replayed trail.
  const replayMarker = new THREE.Mesh(
    new THREE.SphereGeometry(0.018, 16, 12),
    new THREE.MeshBasicMaterial({ color: 0x22d3ee }),
  );
  replayMarker.visible = false;
  scene.add(replayMarker);

  // --- Per-joint limit rings --------------------------------------------
  const limitRings = JOINT_NAMES.map((name, index) => {
    const ring = new THREE.Mesh(
      new THREE.TorusGeometry(0.09 + index * 0.012, 0.004, 8, 48),
      new THREE.MeshBasicMaterial({ color: 0xf59e0b, transparent: true, opacity: 0.55 }),
    );
    ring.visible = false;
    scene.add(ring);
    return { name, ring };
  });

  let robot = null;
  let latestValues = null;
  let failed = false;
  let live = false;
  let currentModel = robotModel;
  let highlightIndex = -1;
  let ringsVisible = false;
  let trailVisible = true;

  function refreshStatus() {
    if (failed) return;
    status.textContent = robot
      ? `${currentModel} model loaded · ${live ? 'live pose' : 'no live telemetry; pose held'}`
      : `Loading ${currentModel} model and meshes…`;
  }

  const manager = new THREE.LoadingManager();
  manager.onError = () => {
    failed = true;
    status.textContent = '3D asset load error — telemetry remains available';
    log('A robot model asset could not be loaded', 'bad');
  };
  manager.onLoad = () => {
    robot?.traverse((object) => {
      if (!object.isMesh) return;
      const materials = Array.isArray(object.material) ? object.material : [object.material];
      materials.filter(Boolean).forEach((material) => {
        if ('roughness' in material) material.roughness = 0.7;
        if ('metalness' in material) material.metalness = 0.15;
      });
    });
    refreshStatus();
  };
  const loader = new URDFLoader(manager);

  function syncRings() {
    if (!robot) return;
    limitRings.forEach(({ name, ring }, index) => {
      const joint = robot.joints?.[name];
      if (!joint) return;
      const worldPosition = new THREE.Vector3();
      joint.getWorldPosition(worldPosition);
      ring.position.copy(worldPosition);
      ring.quaternion.copy(joint.getWorldQuaternion(new THREE.Quaternion()));
      ring.visible = ringsVisible;
      const active = highlightIndex === -1 || highlightIndex === index;
      ring.material.opacity = active ? (highlightIndex === index ? 0.95 : 0.55) : 0.12;
    });
  }

  function loadModel(model) {
    currentModel = model;
    const path = urdfPath || `/${String(model).toLowerCase()}.urdf`;
    loader.load(path, (loaded) => {
      robot = loaded;
      robot.rotation.x = -Math.PI / 2;
      prepareRobot(robot);
      scene.add(robot);
      applyJointValues(robot, latestValues);
      syncRings();
      refreshStatus();
    }, undefined, () => manager.onError());
  }
  loadModel(robotModel);

  const resize = () => {
    const width = element.clientWidth;
    const height = element.clientHeight;
    if (!width || !height) return;
    camera.aspect = width / height;
    camera.updateProjectionMatrix();
    renderer.setSize(width, height);
  };
  new ResizeObserver(resize).observe(element);
  resize();
  renderer.setAnimationLoop(() => {
    controls.update();
    renderer.render(scene, camera);
  });

  const resetView = () => {
    camera.position.set(...DEFAULT_CAMERA.position);
    controls.target.set(...DEFAULT_CAMERA.target);
    controls.update();
  };
  const toggleGrid = () => {
    grid.visible = !grid.visible;
    axes.visible = grid.visible;
    return grid.visible;
  };

  /** Draw a recorded TCP path. Points arrive as [x, y, z] triples. */
  function setTrail(track, { visible = true } = {}) {
    const points = decimateTrack(track, TRAIL_CAPACITY);
    trailCount = points.length;
    for (let index = 0; index < trailCount; index += 1) {
      const point = points[index];
      trailPositions[index * 3] = point[0];
      trailPositions[index * 3 + 1] = point[1];
      trailPositions[index * 3 + 2] = point[2];
    }
    trailGeometry.setDrawRange(0, trailCount);
    trailGeometry.attributes.position.needsUpdate = true;
    trailGeometry.computeBoundingSphere();
    trailVisible = visible;
    trailLine.visible = visible && trailCount > 1;
    return trailCount;
  }

  function clearTrail() {
    trailCount = 0;
    trailGeometry.setDrawRange(0, 0);
    trailLine.visible = false;
    replayMarker.visible = false;
  }

  /** Move the cyan marker to a 3D position; pass null to hide it. */
  function setReplayMarker(position) {
    if (!position) {
      replayMarker.visible = false;
      return;
    }
    replayMarker.position.set(position[0], position[1], position[2]);
    replayMarker.visible = true;
  }

  function setRingsVisible(visible) {
    ringsVisible = Boolean(visible);
    syncRings();
    return ringsVisible;
  }

  /** Highlight one joint (0-based) or pass -1 to clear the highlight. */
  function highlightJoint(index) {
    highlightIndex = Number.isInteger(index) && index >= 0 && index < JOINT_NAMES.length ? index : -1;
    syncRings();
    return highlightIndex;
  }

  function setTheme(theme) {
    const chosen = theme === 'light' ? 'light' : 'dark';
    scene.background = new THREE.Color(SCENE_BACKGROUNDS[chosen]);
    const [major, minor] = GRID_COLOURS[chosen];
    const wasVisible = grid.visible;
    scene.remove(grid);
    grid.geometry.dispose();
    grid.material.dispose();
    grid = new THREE.GridHelper(2.5, 25, major, minor);
    grid.visible = wasVisible;
    scene.add(grid);
  }

  /** Render one frame and return a PNG data URL, for report export. */
  function snapshotImage() {
    renderer.render(scene, camera);
    return renderer.domElement.toDataURL('image/png');
  }

  return {
    update(values) {
      latestValues = values;
      live = values !== null;
      applyJointValues(robot, values);
      syncRings();
      refreshStatus();
    },
    resetView,
    toggleGrid,
    setTrail,
    clearTrail,
    setReplayMarker,
    setRingsVisible,
    highlightJoint,
    setTheme,
    snapshotImage,
    loadModel,
    get trailPoints() {
      return trailCount;
    },
    dispose() {
      renderer.setAnimationLoop(null);
      renderer.dispose();
    },
  };
}
