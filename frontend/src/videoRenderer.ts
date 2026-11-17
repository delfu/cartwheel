export type VideoView = "original" | "mask" | "isolated";

export function createVideoRenderer(canvas: HTMLCanvasElement) {
  const gl = canvas.getContext("webgl2", {
    alpha: false,
    antialias: false,
    depth: false,
  });
  if (!gl)
    throw new Error(
      "WebGL 2 is required for video compositing in this browser.",
    );
  const vertex = `#version 300 es
    in vec2 position;
    out vec2 uv;
    void main() { uv = position * 0.5 + 0.5; gl_Position = vec4(position, 0, 1); }`;
  const onCompile = (type: number, source: string) => {
    const shader = gl.createShader(type);
    if (!shader) throw new Error("Could not create a video shader.");
    gl.shaderSource(shader, source);
    gl.compileShader(shader);
    if (!gl.getShaderParameter(shader, gl.COMPILE_STATUS)) {
      const message = gl.getShaderInfoLog(shader);
      gl.deleteShader(shader);
      throw new Error(`Video shader failed: ${message}`);
    }
    return shader;
  };
  const onProgram = (fragment: string) => {
    const program = gl.createProgram();
    if (!program) throw new Error("Could not create a video renderer.");
    const shaders = [
      onCompile(gl.VERTEX_SHADER, vertex),
      onCompile(gl.FRAGMENT_SHADER, fragment),
    ];
    for (const shader of shaders) gl.attachShader(program, shader);
    gl.bindAttribLocation(program, 0, "position");
    gl.linkProgram(program);
    for (const shader of shaders) gl.deleteShader(shader);
    if (!gl.getProgramParameter(program, gl.LINK_STATUS)) {
      const message = gl.getProgramInfoLog(program);
      gl.deleteProgram(program);
      throw new Error(`Video renderer failed: ${message}`);
    }
    return program;
  };
  const maskProgram = onProgram(`#version 300 es
    precision highp float;
    in vec2 uv;
    uniform sampler2D source;
    out vec4 color;
    void main() { color = vec4(vec3(step(0.5, texture(source, uv).r)), 1); }`);
  const outputProgram = onProgram(`#version 300 es
    precision highp float;
    in vec2 uv;
    uniform sampler2D source;
    uniform sampler2D mask;
    uniform int view;
    out vec4 color;
    void main() {
      float selected = texture(mask, uv).r;
      vec3 original = texture(source, uv).rgb;
      color = vec4(view == 0 ? original : (view == 1 ? vec3(selected) : original * selected), 1);
    }`);
  const buffer = gl.createBuffer();
  const vao = gl.createVertexArray();
  const framebuffer = gl.createFramebuffer();
  if (!buffer || !vao || !framebuffer)
    throw new Error("Could not allocate video rendering resources.");
  const onTexture = () => {
    const texture = gl.createTexture();
    if (!texture) throw new Error("Could not allocate video texture.");
    gl.bindTexture(gl.TEXTURE_2D, texture);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.NEAREST);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.NEAREST);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
    return texture;
  };
  const union = onTexture();
  gl.texImage2D(
    gl.TEXTURE_2D,
    0,
    gl.RGBA8,
    canvas.width,
    canvas.height,
    0,
    gl.RGBA,
    gl.UNSIGNED_BYTE,
    null,
  );
  gl.bindFramebuffer(gl.FRAMEBUFFER, framebuffer);
  gl.framebufferTexture2D(
    gl.FRAMEBUFFER,
    gl.COLOR_ATTACHMENT0,
    gl.TEXTURE_2D,
    union,
    0,
  );
  if (gl.checkFramebufferStatus(gl.FRAMEBUFFER) !== gl.FRAMEBUFFER_COMPLETE)
    throw new Error(
      "Video dimensions exceed this device's rendering capabilities.",
    );
  const black = onTexture();
  gl.texImage2D(
    gl.TEXTURE_2D,
    0,
    gl.RGBA,
    1,
    1,
    0,
    gl.RGBA,
    gl.UNSIGNED_BYTE,
    new Uint8Array([0, 0, 0, 255]),
  );
  gl.bindVertexArray(vao);
  gl.bindBuffer(gl.ARRAY_BUFFER, buffer);
  gl.bufferData(
    gl.ARRAY_BUFFER,
    new Float32Array([-1, -1, 1, -1, -1, 1, -1, 1, 1, -1, 1, 1]),
    gl.STATIC_DRAW,
  );
  gl.enableVertexAttribArray(0);
  gl.vertexAttribPointer(0, 2, gl.FLOAT, false, 0, 0);
  gl.pixelStorei(gl.UNPACK_FLIP_Y_WEBGL, true);
  const textures = new Map<
    HTMLVideoElement,
    { texture: WebGLTexture; frame: number }
  >();
  const maskSource = gl.getUniformLocation(maskProgram, "source");
  const outputSource = gl.getUniformLocation(outputProgram, "source");
  const outputMask = gl.getUniformLocation(outputProgram, "mask");
  const outputView = gl.getUniformLocation(outputProgram, "view");
  const onUpload = (video: HTMLVideoElement, frame: number) => {
    let entry = textures.get(video);
    if (!entry) {
      entry = { texture: onTexture(), frame: -1 };
      textures.set(video, entry);
    }
    gl.bindTexture(gl.TEXTURE_2D, entry.texture);
    if (entry.frame !== frame) {
      gl.texImage2D(
        gl.TEXTURE_2D,
        0,
        gl.RGBA,
        gl.RGBA,
        gl.UNSIGNED_BYTE,
        video,
      );
      entry.frame = frame;
    }
    return entry.texture;
  };
  return {
    onDraw(
      original: HTMLVideoElement,
      masks: HTMLVideoElement[],
      view: VideoView,
      frame: number,
    ) {
      if (gl.isContextLost())
        throw new Error(
          "Graphics context lost. Reload to restore video playback.",
        );
      gl.bindVertexArray(vao);
      gl.viewport(0, 0, canvas.width, canvas.height);
      gl.activeTexture(gl.TEXTURE0);
      if (view !== "original") {
        gl.bindFramebuffer(gl.FRAMEBUFFER, framebuffer);
        gl.clearColor(0, 0, 0, 1);
        gl.clear(gl.COLOR_BUFFER_BIT);
        gl.useProgram(maskProgram);
        gl.uniform1i(maskSource, 0);
        gl.enable(gl.BLEND);
        gl.blendEquation(gl.MAX);
        gl.blendFunc(gl.ONE, gl.ONE);
        for (const mask of masks) {
          onUpload(mask, frame);
          gl.drawArrays(gl.TRIANGLES, 0, 6);
        }
        gl.disable(gl.BLEND);
      }
      gl.bindFramebuffer(gl.FRAMEBUFFER, null);
      const source = view === "mask" ? black : onUpload(original, frame);
      gl.useProgram(outputProgram);
      gl.activeTexture(gl.TEXTURE0);
      gl.bindTexture(gl.TEXTURE_2D, source);
      gl.uniform1i(outputSource, 0);
      gl.activeTexture(gl.TEXTURE1);
      gl.bindTexture(gl.TEXTURE_2D, union);
      gl.uniform1i(outputMask, 1);
      gl.uniform1i(
        outputView,
        view === "original" ? 0 : view === "mask" ? 1 : 2,
      );
      gl.drawArrays(gl.TRIANGLES, 0, 6);
    },
    onKeepVideos(videos: HTMLVideoElement[]) {
      for (const [video, entry] of textures)
        if (!videos.includes(video)) {
          gl.deleteTexture(entry.texture);
          textures.delete(video);
        }
    },
    onDispose() {
      for (const entry of textures.values()) gl.deleteTexture(entry.texture);
      textures.clear();
      gl.deleteTexture(union);
      gl.deleteTexture(black);
      gl.deleteFramebuffer(framebuffer);
      gl.deleteBuffer(buffer);
      gl.deleteVertexArray(vao);
      gl.deleteProgram(maskProgram);
      gl.deleteProgram(outputProgram);
    },
  };
}
