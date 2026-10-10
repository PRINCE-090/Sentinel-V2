/* eslint-disable react/no-unknown-property */
import { forwardRef, useEffect, useRef } from "react";
import { Canvas, useFrame, useThree } from "@react-three/fiber";
import { EffectComposer, wrapEffect } from "@react-three/postprocessing";
import { Effect } from "postprocessing";
import * as THREE from "three";

type DitherProps = {
  waveColor?: [number, number, number];
  disableAnimation?: boolean;
  enableMouseInteraction?: boolean;
  mouseRadius?: number;
  colorNum?: number;
  pixelSize?: number;
  waveAmplitude?: number;
  waveFrequency?: number;
  waveSpeed?: number;
};

const vertexShader = `varying vec2 vUv; void main() { vUv = uv; gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0); }`;
const waveShader = `
precision highp float;
uniform vec2 resolution; uniform float time; uniform float waveSpeed; uniform float waveFrequency; uniform float waveAmplitude; uniform vec3 waveColor; uniform vec2 mousePos; uniform int enableMouseInteraction; uniform float mouseRadius;
vec4 mod289(vec4 x){return x-floor(x*(1.0/289.0))*289.0;} vec4 permute(vec4 x){return mod289(((x*34.0)+1.0)*x);} vec4 taylorInvSqrt(vec4 r){return 1.79284291400159-0.85373472095314*r;} vec2 fade(vec2 t){return t*t*t*(t*(t*6.0-15.0)+10.0);}
float noise(vec2 P){vec4 Pi=floor(P.xyxy)+vec4(0.,0.,1.,1.); vec4 Pf=fract(P.xyxy)-vec4(0.,0.,1.,1.); Pi=mod289(Pi); vec4 ix=Pi.xzxz; vec4 iy=Pi.yyww; vec4 fx=Pf.xzxz; vec4 fy=Pf.yyww; vec4 i=permute(permute(ix)+iy); vec4 gx=fract(i*(1.0/41.0))*2.0-1.0; vec4 gy=abs(gx)-.5; vec4 tx=floor(gx+.5); gx-=tx; vec2 g00=vec2(gx.x,gy.x),g10=vec2(gx.y,gy.y),g01=vec2(gx.z,gy.z),g11=vec2(gx.w,gy.w); vec4 norm=taylorInvSqrt(vec4(dot(g00,g00),dot(g01,g01),dot(g10,g10),dot(g11,g11))); g00*=norm.x;g01*=norm.y;g10*=norm.z;g11*=norm.w; float n00=dot(g00,vec2(fx.x,fy.x)),n10=dot(g10,vec2(fx.y,fy.y)),n01=dot(g01,vec2(fx.z,fy.z)),n11=dot(g11,vec2(fx.w,fy.w)); vec2 f=fade(Pf.xy); return 2.3*mix(mix(n00,n01,f.x),mix(n10,n11,f.x),f.y);}
float fbm(vec2 p){float value=0.;float amp=1.;float freq=waveFrequency;for(int i=0;i<4;i++){value+=amp*abs(noise(p));p*=freq;amp*=waveAmplitude;}return value;}
void main(){vec2 uv=gl_FragCoord.xy/resolution.xy-.5;uv.x*=resolution.x/resolution.y;vec2 drift=vec2(time*waveSpeed,time*waveSpeed*.38);float f=fbm(uv+drift+fbm(uv-drift));if(enableMouseInteraction==1){vec2 mouseNDC=(mousePos/resolution-.5)*vec2(1.,-1.);mouseNDC.x*=resolution.x/resolution.y;f-=.5*(1.-smoothstep(0.,mouseRadius,length(uv-mouseNDC)));}gl_FragColor=vec4(mix(vec3(0.),waveColor,f),1.);}`;
const ditherShader = `
uniform float colorNum; uniform float pixelSize;
const float m[64]=float[64](0./64.,48./64.,12./64.,60./64.,3./64.,51./64.,15./64.,63./64.,32./64.,16./64.,44./64.,28./64.,35./64.,19./64.,47./64.,31./64.,8./64.,56./64.,4./64.,52./64.,11./64.,59./64.,7./64.,55./64.,40./64.,24./64.,36./64.,20./64.,43./64.,27./64.,39./64.,23./64.,2./64.,50./64.,14./64.,62./64.,1./64.,49./64.,13./64.,61./64.,34./64.,18./64.,46./64.,30./64.,33./64.,17./64.,47./64.,31./64.,10./64.,58./64.,6./64.,54./64.,9./64.,57./64.,5./64.,53./64.,42./64.,22./64.,38./64.,20./64.,41./64.,25./64.,37./64.,21./64.);
void mainImage(in vec4 inputColor,in vec2 uv,out vec4 outputColor){vec2 normalized=pixelSize/resolution;vec2 snapped=normalized*floor(uv/normalized);vec4 color=texture2D(inputBuffer,snapped);vec2 c=floor(uv*resolution/pixelSize);int x=int(mod(c.x,8.));int y=int(mod(c.y,8.));float stepSize=1./(colorNum-1.);color.rgb=clamp(color.rgb+(m[y*8+x]-.25)*stepSize-.2,0.,1.);outputColor=vec4(floor(color.rgb*(colorNum-1.)+.5)/(colorNum-1.),color.a);}`;

class RetroEffect extends Effect {
  constructor() {
    const uniforms = new Map([["colorNum", new THREE.Uniform(4)], ["pixelSize", new THREE.Uniform(2)]]);
    super("SentinelDither", ditherShader, { uniforms });
  }
}
const WrappedRetro = wrapEffect(RetroEffect) as unknown as React.ComponentType<{ colorNum: number; pixelSize: number }>;

function Waves({ waveColor, waveSpeed, waveFrequency, waveAmplitude, disableAnimation, enableMouseInteraction, mouseRadius, colorNum, pixelSize }: Required<DitherProps>) {
  const mouse = useRef(new THREE.Vector2());
  const { viewport, size, gl } = useThree();
  const uniforms = useRef({ time: new THREE.Uniform(0), resolution: new THREE.Uniform(new THREE.Vector2()), waveSpeed: new THREE.Uniform(waveSpeed), waveFrequency: new THREE.Uniform(waveFrequency), waveAmplitude: new THREE.Uniform(waveAmplitude), waveColor: new THREE.Uniform(new THREE.Color(...waveColor)), mousePos: new THREE.Uniform(new THREE.Vector2()), enableMouseInteraction: new THREE.Uniform(enableMouseInteraction ? 1 : 0), mouseRadius: new THREE.Uniform(mouseRadius) });
  useEffect(() => { uniforms.current.resolution.value.set(size.width * gl.getPixelRatio(), size.height * gl.getPixelRatio()); }, [gl, size]);
  useFrame(({ clock }) => {
    const u = uniforms.current;
    if (!disableAnimation) u.time.value = clock.getElapsedTime();
    u.waveSpeed.value = waveSpeed; u.waveFrequency.value = waveFrequency; u.waveAmplitude.value = waveAmplitude; u.waveColor.value.set(...waveColor); u.enableMouseInteraction.value = enableMouseInteraction ? 1 : 0; u.mouseRadius.value = mouseRadius; u.mousePos.value.copy(mouse.current);
  });
  return <>
    <mesh scale={[viewport.width, viewport.height, 1]}><planeGeometry args={[1, 1]} /><shaderMaterial vertexShader={vertexShader} fragmentShader={waveShader} uniforms={uniforms.current} /></mesh>
    <EffectComposer><WrappedRetro colorNum={colorNum} pixelSize={pixelSize} /></EffectComposer>
    <mesh visible={false} onPointerMove={(event) => { const rect = gl.domElement.getBoundingClientRect(); const dpr = gl.getPixelRatio(); mouse.current.set((event.clientX - rect.left) * dpr, (event.clientY - rect.top) * dpr); }} scale={[viewport.width, viewport.height, 1]} position={[0, 0, 0.01]}><planeGeometry args={[1, 1]} /><meshBasicMaterial transparent opacity={0} /></mesh>
  </>;
}

export default function Dither({ waveColor = [0.5, 0.5, 0.5], disableAnimation = false, enableMouseInteraction = true, mouseRadius = 0.3, colorNum = 4, pixelSize = 2, waveAmplitude = 0.3, waveFrequency = 3, waveSpeed = 0.05 }: DitherProps) {
  return <Canvas className="dither-container" frameloop="always" camera={{ position: [0, 0, 6] }} dpr={1} gl={{ antialias: true, powerPreference: "low-power" }}><Waves waveColor={waveColor} disableAnimation={disableAnimation} enableMouseInteraction={enableMouseInteraction} mouseRadius={mouseRadius} colorNum={colorNum} pixelSize={pixelSize} waveAmplitude={waveAmplitude} waveFrequency={waveFrequency} waveSpeed={waveSpeed} /></Canvas>;
}
