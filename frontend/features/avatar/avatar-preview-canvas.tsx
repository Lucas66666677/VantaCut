"use client";

import { useEffect, useRef } from "react";
import * as THREE from "three";

export interface AvatarPreviewFrame { blendshapes?: Record<string, number>; bones?: Record<string, { rotation_z?: number; yaw?: number; pitch?: number; roll?: number }>; }

export function AvatarPreviewCanvas({ frame }: { frame?: AvatarPreviewFrame }) {
  const hostRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const host = hostRef.current; if (!host) return;
    const scene = new THREE.Scene(); const camera = new THREE.PerspectiveCamera(35, 16 / 9, .1, 100); camera.position.set(0, 1.4, 5);
    const renderer = new THREE.WebGLRenderer({ alpha: true, antialias: true }); renderer.setSize(640, 360); host.appendChild(renderer.domElement);
    const light = new THREE.DirectionalLight(0xffffff, 2); light.position.set(2, 4, 5); scene.add(light, new THREE.AmbientLight(0x667799, 1));
    const group = new THREE.Group(); scene.add(group); const head = new THREE.Mesh(new THREE.SphereGeometry(.7, 36, 24), new THREE.MeshStandardMaterial({ color: 0x75b9ff, roughness: .58 })); head.position.y = 1.35; group.add(head);
    const torso = new THREE.Mesh(new THREE.CapsuleGeometry(.55, 1.3, 8, 16), new THREE.MeshStandardMaterial({ color: 0x1d4ed8 })); torso.position.y = .2; group.add(torso);
    const render = () => { const mouth = frame?.blendshapes?.jawOpen ?? 0; head.scale.y = 1 + mouth * .08; const rotation = frame?.bones?.head; head.rotation.set((rotation?.pitch ?? 0) * Math.PI / 180, (rotation?.yaw ?? 0) * Math.PI / 180, (rotation?.roll ?? 0) * Math.PI / 180); renderer.render(scene, camera); };
    render(); return () => { renderer.dispose(); host.removeChild(renderer.domElement); };
  }, [frame]);
  return <div ref={hostRef} className="overflow-hidden rounded-xl bg-gradient-to-b from-slate-800 to-slate-950" aria-label="虛擬主播 Three.js 預覽" />;
}
