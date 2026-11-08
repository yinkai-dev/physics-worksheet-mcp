"""Small closed LaTeX language and workspace path confinement."""
from pathlib import Path, PurePosixPath, PureWindowsPath
import re


class SafetyError(ValueError):
    pass


MATH_COMMANDS = frozenset("""
frac dfrac tfrac sqrt cdot times div pm mp le leq ge geq ll gg ne neq approx sim simeq
equiv propto infty degree circ angle perp parallel sin cos tan cot arcsin arccos
arctan log ln exp max min lim sum prod int iint oint partial nabla Delta delta
alpha beta gamma Gamma theta Theta lambda Lambda mu nu rho sigma Sigma tau phi
Phi omega Omega epsilon varepsilon pi Pi eta kappa zeta chi psi Psi varphi vartheta
vec overrightarrow overline underline hat bar dot ddot mathrm mathbf mathit text
left right lvert rvert vert Vert lVert rVert cdots ldots dots quad qquad forall
exists in notin subset supset cup cap to rightarrow leftarrow leftrightarrow
Rightarrow Leftarrow Leftrightarrow
""".split())
_TEXT_ESCAPES = {"\\": r"\textbackslash{}", "{": r"\{", "}": r"\}", "#": r"\#",
                 "%": r"\%", "$": r"\$", "&": r"\&", "_": r"\_", "~": r"\textasciitilde{}", "^": r"\textasciicircum{}"}
_UNICODE_MATH = dict(zip('≈≠≤≥±×÷∞∝⊥∥⊐→←↔',
                        [r'\approx',r'\neq',r'\leq',r'\geq',r'\pm',r'\times',r'\div',r'\infty',r'\propto',r'\perp',r'\parallel',r'\sqsupset',r'\rightarrow',r'\leftarrow',r'\leftrightarrow']))
_UNICODE_MATH.update(dict(zip('αβγδθλμνρστφψωΔΣΩπ',
                             ['\\'+name for name in ['alpha','beta','gamma','delta','theta','lambda','mu','nu','rho','sigma','tau','phi','psi','omega','Delta','Sigma','Omega','pi']])))
_UNICODE_MATH.update({char:'{}_{'+str(i)+'}' for i,char in enumerate('₀₁₂₃₄₅₆₇₈₉')})
_UNICODE_MATH.update({char:'{}^{'+str(i)+'}' for i,char in enumerate('⁰¹²³⁴⁵⁶⁷⁸⁹')})
_UNICODE_MATH.update({'∠':r'\angle','₊':r'{}_{+}','₋':r'{}_{-}','⁺':r'{}^{+}','⁻':r'{}^{-}',
                      '℃':r'{}^{\circ}\mathrm{C}','℉':r'{}^{\circ}\mathrm{F}','°':r'{}^{\circ}'})


def normalize_math_unicode(value: str) -> str:
    """Map literal Unicode math glyphs once, leaving validated TeX unchanged."""
    return ''.join((_UNICODE_MATH[char]+'{}') if char in _UNICODE_MATH else char for char in value)


def escape_text(value: str) -> str:
    """Escape each original character once; never reprocess inserted commands."""
    escaped="".join((r'\ensuremath{'+_UNICODE_MATH[char]+'}') if char in _UNICODE_MATH else _TEXT_ESCAPES.get(char,char) for char in value).replace("\n", "\\par\n")
    return re.sub(r'(?:\\_){2,}',lambda match:r'\allowbreak{}'+match[0]+r'\allowbreak{}',escaped)


def validate_math(value: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 1200:
        raise SafetyError("unsafe-math: expected 1..1200 characters")
    # A literal ~ is TeX's standard nonbreaking space, used by source OCR in
    # units such as \mathrm{~m}. Preserve it; macro definitions and arbitrary
    # control sequences remain forbidden below.
    if "^^" in value or any(char in value for char in "$#&") or re.search(r"(?<!\\)%", value):
        raise SafetyError("unsafe-math: TeX delimiters, comments or expansion are forbidden")
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise SafetyError("unsafe-math: control characters are forbidden")
    depth, commands, index = 0, 0, 0
    while index < len(value):
        char = value[index]
        if char == "\\":
            index += 1
            if index >= len(value):
                raise SafetyError("unsafe-math: incomplete escape")
            match = re.match(r"[A-Za-z]+", value[index:])
            if match:
                command = match.group(0)
                if command not in MATH_COMMANDS:
                    raise SafetyError(f"unsafe-math: command \\{command} is not allowed")
                index += len(command)
            elif value[index] in "{},;! |%":
                index += 1
            else:
                raise SafetyError("unsafe-math: escape is not allowed")
            commands += 1
            if commands > 150:
                raise SafetyError("unsafe-math: too many commands")
            continue
        if char == "{":
            depth += 1
            if depth > 20:
                raise SafetyError("unsafe-math: nesting limit exceeded")
        elif char == "}":
            depth -= 1
            if depth < 0:
                raise SafetyError("unsafe-math: unbalanced braces")
        index += 1
    if depth:
        raise SafetyError("unsafe-math: unbalanced braces")
    return value


def safe_workspace_path(root: Path, relative: str) -> Path:
    if not isinstance(relative, str) or not relative or "\x00" in relative:
        raise SafetyError("unsafe-path: path must be a nonempty relative string")
    win, posix = PureWindowsPath(relative), PurePosixPath(relative.replace("\\", "/"))
    if win.drive or win.is_absolute() or posix.is_absolute() or ".." in posix.parts:
        raise SafetyError("unsafe-path: absolute paths and parent traversal are forbidden")
    base = root.resolve()
    target = (base / Path(*posix.parts)).resolve()
    if not target.is_relative_to(base):
        raise SafetyError("unsafe-path: path escapes workspace (including symlinks)")
    return target
