"""Diffuse terrain layers share UVs and use vertex alpha as a mix weight."""

import ctypes as ct

VERTEX = b"""#version 120
varying vec2 uv;
varying vec2 uv2;
varying vec4 tint;
varying float weight;
void main() {
    gl_Position = ftransform();
    uv = gl_MultiTexCoord0.xy;
    uv2 = gl_MultiTexCoord1.xy;
    weight = gl_MultiTexCoord1.z;
    tint = gl_Color;
}
"""

FRAGMENT = b"""#version 120
uniform sampler2D firstLayer;
uniform sampler2D secondLayer;
varying vec2 uv;
varying vec2 uv2;
varying vec4 tint;
varying float weight;
void main() {
    vec4 first = texture2D(firstLayer, uv);
    vec4 second = texture2D(secondLayer, uv2);
    // PSP vertex alpha marks coverage of the second diffuse layer.
    float coverage = clamp((1.0 - weight) * first.a, 0.0, 1.0);
    float alpha = coverage + second.a * (1.0 - coverage);
    vec3 rgb = first.rgb * coverage + second.rgb * second.a * (1.0 - coverage);
    gl_FragColor = vec4(rgb / max(alpha, 0.000001) * tint.rgb, alpha * tint.a);
}
"""


class TextureBlendProgram:
    def __init__(self):
        from ts2psp.rendering.opengl import _fn, SinOpenGL

        u, i, p = ct.c_uint, ct.c_int, ct.c_void_p
        create = _fn("glCreateShader", u, u)
        source = _fn("glShaderSource", None, u, i, p, p)
        compile_shader = _fn("glCompileShader", None, u)
        shader_status = _fn("glGetShaderiv", None, u, u, p)
        shader_log = _fn("glGetShaderInfoLog", None, u, i, p, p)
        delete_shader = _fn("glDeleteShader", None, u)
        create_program = _fn("glCreateProgram", u)
        attach = _fn("glAttachShader", None, u, u)
        link = _fn("glLinkProgram", None, u)
        program_status = _fn("glGetProgramiv", None, u, u, p)
        program_log = _fn("glGetProgramInfoLog", None, u, i, p, p)
        self.use = _fn("glUseProgram", None, u)
        location = _fn("glGetUniformLocation", i, u, ct.c_char_p)
        uniform = _fn("glUniform1i", None, i, i)
        self.active = _fn("glActiveTexture", None, u)
        self.client_active = _fn("glClientActiveTexture", None, u)
        self.program = create_program()
        shaders = []
        try:
            for kind, code in ((0x8B31, VERTEX), (0x8B30, FRAGMENT)):
                shader = create(kind)
                shaders.append(shader)
                text = ct.c_char_p(code)
                source(shader, 1, ct.byref(text), None)
                compile_shader(shader)
                status = i()
                shader_status(shader, 0x8B81, ct.byref(status))
                if not status.value:
                    log = ct.create_string_buffer(4096)
                    shader_log(shader, len(log), None, log)
                    raise SinOpenGL(
                        "Terrain shader: " + log.value.decode("utf-8", "replace")
                    )
                attach(self.program, shader)
            link(self.program)
            status = i()
            program_status(self.program, 0x8B82, ct.byref(status))
            if not status.value:
                log = ct.create_string_buffer(4096)
                program_log(self.program, len(log), None, log)
                raise SinOpenGL(
                    "Terrain shader link: " + log.value.decode("utf-8", "replace")
                )
            self.use(self.program)
            uniform(location(self.program, b"firstLayer"), 0)
            uniform(location(self.program, b"secondLayer"), 1)
        finally:
            self.use(0)
            for shader in shaders:
                delete_shader(shader)

    def begin(self, context, texture, weights):
        self.use(self.program)
        self.active(0x84C1)
        context.bind_textura(0x0DE1, texture)
        self.active(0x84C0)
        self.client_active(0x84C1)
        context.activar_array(0x8078)
        context.punteros_uv(3, 0x1406, 0, weights.ctypes.data)
        self.client_active(0x84C0)

    def end(self, context):
        self.use(0)
        self.client_active(0x84C1)
        context.desactivar_array(0x8078)
        self.client_active(0x84C0)
