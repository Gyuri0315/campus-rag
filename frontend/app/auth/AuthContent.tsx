"use client";

import {
  useEffect,
  useId,
  useMemo,
  useRef,
  useState,
  type FormEvent,
  type KeyboardEvent as ReactKeyboardEvent,
  type ReactNode,
} from "react";
import { useRouter, useSearchParams } from "next/navigation";
import Image from "next/image";
import Link from "next/link";

import { useAuth } from "@/app/context/AuthContext";
import { authErrorMessage, safeNextPath } from "@/app/lib/safeRedirect";

const NAVY = "#25348B";
const NAVY_MUTED = "rgba(37,52,139,0.45)";

// 실제 강제는 auth.users 트리거(enforce_school_email_domain, 010 + 013
// 마이그레이션)에서 하고, 이건 사용자에게 즉시 피드백을 주기 위한 클라이언트
// 측 사전 검증이다. office.pknu.ac.kr는 행정실 등 교직원 계정으로 추정.
const SCHOOL_DOMAINS = ["pknu.ac.kr", "pukyong.ac.kr", "office.pknu.ac.kr"] as const;
type SchoolDomain = (typeof SCHOOL_DOMAINS)[number];
const SCHOOL_EMAIL_RE = new RegExp(
  `^[^\\s@]+@(${SCHOOL_DOMAINS.map((d) => d.replace(/\./g, "\\.")).join("|")})$`,
  "i",
);

function isSchoolDomain(value: string): value is SchoolDomain {
  return (SCHOOL_DOMAINS as readonly string[]).includes(value);
}

// 이메일 아이디에 쓸 수 있는 글자(주소에 흔히 쓰는 것만).
const LOCAL_PART_RE = /^[A-Za-z0-9._%+-]+$/;

// 회원가입 비밀번호 기준. 화면 안내와 제출 검사가 같은 목록을 쓴다. 우회를 막는 실제 강제는
// Supabase Auth 설정(Password requirements)에서 같은 기준으로 해야 한다 — 여기는 안내용이다.
// 72자 상한: Supabase(bcrypt)는 72바이트 뒤를 조용히 버려, 더 긴 비밀번호는 앞 72자만 쓰인다.
const PASSWORD_MAX = 72;
const PASSWORD_RULES = [
  { id: "length", label: "8자 이상", test: (pw: string) => pw.length >= 8 && pw.length <= PASSWORD_MAX },
  { id: "letter", label: "영문", test: (pw: string) => /[A-Za-z]/.test(pw) },
  { id: "digit", label: "숫자", test: (pw: string) => /\d/.test(pw) },
] as const;

const IconCheck = () => (
  <svg width="10" height="10" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
    <polyline points="3,8.5 6.5,12 13,4.5" />
  </svg>
);

// 회원가입에서만 보이는 부분을 부드럽게 펼치고 접는다(grid 행 0fr↔1fr). 접혔을 때는 부모의
// gap만큼 음수 여백을 줘 빈틈을 없애고, 포커스·클릭·화면 읽기에서도 빼낸다(inert).
function Collapse({ open, gap, children }: { open: boolean; gap: number; children: ReactNode }) {
  return (
    <div
      inert={!open}
      aria-hidden={!open}
      className="grid transition-[grid-template-rows,opacity,margin] duration-300 ease-out motion-reduce:transition-none"
      style={{ gridTemplateRows: open ? "1fr" : "0fr", opacity: open ? 1 : 0, marginTop: open ? 0 : -gap }}
    >
      <div className="min-h-0 overflow-hidden">{children}</div>
    </div>
  );
}

// 비밀번호 기준을 입력하는 동안 하나씩 채워 보여준다(채운 기준은 진한 남색 + 체크).
function PasswordRules({ password, id }: { password: string; id: string }) {
  return (
    <ul id={id} className="flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px]" aria-label="비밀번호 기준">
      {PASSWORD_RULES.map((rule) => {
        const met = rule.test(password);
        return (
          <li
            key={rule.id}
            className="flex items-center gap-1 transition-colors duration-200 motion-reduce:transition-none"
            style={{ color: met ? NAVY : "rgba(37,52,139,0.38)", fontWeight: met ? 600 : 400 }}
          >
            <span className="flex h-3 w-3 items-center justify-center" style={{ opacity: met ? 1 : 0.5 }}>
              <IconCheck />
            </span>
            {rule.label}
            <span className="sr-only">{met ? " 충족" : " 미충족"}</span>
          </li>
        );
      })}
    </ul>
  );
}

// ── 아이콘 ────────────────────────────────────────────────────────────────────
const IconArrowLeft = () => (
  <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
    <line x1="13" y1="8" x2="3" y2="8" />
    <polyline points="7,3 2,8 7,13" />
  </svg>
);

const IconSpinner = () => (
  <svg
    width="14"
    height="14"
    viewBox="0 0 16 16"
    fill="none"
    className="animate-spin"
    aria-hidden
  >
    <circle cx="8" cy="8" r="6" stroke="currentColor" strokeOpacity="0.25" strokeWidth="2" />
    <path d="M14 8a6 6 0 0 0-6-6" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
  </svg>
);

// lucide Eye / EyeOff 와 같은 모양 (앱은 아이콘 라이브러리 없이 인라인 SVG를 쓴다)
const IconEye = () => (
  <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
    <path d="M2.062 12.348a1 1 0 0 1 0-.696 10.75 10.75 0 0 1 19.876 0 1 1 0 0 1 0 .696 10.75 10.75 0 0 1-19.876 0" />
    <circle cx="12" cy="12" r="3" />
  </svg>
);

const IconEyeOff = () => (
  <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
    <path d="M10.733 5.076a10.744 10.744 0 0 1 11.205 6.575 1 1 0 0 1 0 .696 10.747 10.747 0 0 1-1.444 2.49" />
    <path d="M14.084 14.158a3 3 0 0 1-4.242-4.242" />
    <path d="M17.479 17.499a10.75 10.75 0 0 1-15.417-5.151 1 1 0 0 1 0-.696 10.75 10.75 0 0 1 4.446-5.143" />
    <path d="m2 2 20 20" />
  </svg>
);

const IconChevronDown = () => (
  <svg width="12" height="12" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
    <polyline points="4,6 8,10 12,6" />
  </svg>
);

const INPUT_BORDER = "rgba(37,52,139,0.12)";
const INPUT_BORDER_FOCUS = "rgba(37,52,139,0.4)";
const INPUT_BORDER_ERROR = "rgba(197,48,48,0.55)";
const ERROR_RED = "#c53030";

const INPUT_STYLE = {
  background: "rgba(255,255,255,0.85)",
  border: `1.5px solid ${INPUT_BORDER}`,
  color: NAVY,
  caretColor: NAVY,
} as const;
const onFocusBorder = (e: { currentTarget: HTMLElement }) => { e.currentTarget.style.borderColor = INPUT_BORDER_FOCUS; };
const onIdleBorder = (e: { currentTarget: HTMLElement }) => { e.currentTarget.style.borderColor = INPUT_BORDER; };

// 학교 도메인 드롭다운. 기본 <select>는 펼친 목록 모양을 바꿀 수 없어 직접 만든다
// (select-only combobox: 버튼이 포커스를 갖고 aria-activedescendant로 항목을 가리킨다).
function DomainSelect({
  value,
  onChange,
}: {
  value: SchoolDomain;
  onChange: (value: SchoolDomain) => void;
}) {
  const id = useId();
  const listId = `${id}-list`;
  const optionId = (index: number) => `${id}-opt-${index}`;
  const rootRef = useRef<HTMLDivElement>(null);
  const [open, setOpen] = useState(false);
  const [focused, setFocused] = useState(false);
  const [active, setActive] = useState(() => SCHOOL_DOMAINS.indexOf(value));

  useEffect(() => {
    if (!open) return;
    const closeOutside = (e: PointerEvent) => {
      if (!rootRef.current?.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("pointerdown", closeOutside);
    return () => document.removeEventListener("pointerdown", closeOutside);
  }, [open]);

  const openMenu = () => {
    setActive(SCHOOL_DOMAINS.indexOf(value));
    setOpen(true);
  };
  const choose = (index: number) => {
    onChange(SCHOOL_DOMAINS[index]);
    setOpen(false);
  };

  const handleKeyDown = (e: ReactKeyboardEvent<HTMLButtonElement>) => {
    const last = SCHOOL_DOMAINS.length - 1;
    if (!open) {
      if (["ArrowDown", "ArrowUp", "Enter", " "].includes(e.key)) {
        e.preventDefault();
        openMenu();
      }
      return;
    }
    switch (e.key) {
      case "ArrowDown": e.preventDefault(); setActive((i) => Math.min(i + 1, last)); break;
      case "ArrowUp": e.preventDefault(); setActive((i) => Math.max(i - 1, 0)); break;
      case "Home": e.preventDefault(); setActive(0); break;
      case "End": e.preventDefault(); setActive(last); break;
      case "Enter":
      case " ": e.preventDefault(); choose(active); break;
      case "Escape": e.preventDefault(); setOpen(false); break;
      case "Tab": setOpen(false); break;
    }
  };

  return (
    // 폭은 가장 긴 도메인(office.pknu.ac.kr)에 맞추고, 좁은 화면에서는 아이디 칸이 줄어든다.
    <div ref={rootRef} className="relative flex-shrink-0">
      <button
        type="button"
        role="combobox"
        aria-label="이메일 도메인"
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-controls={listId}
        aria-activedescendant={open ? optionId(active) : undefined}
        onClick={() => (open ? setOpen(false) : openMenu())}
        onKeyDown={handleKeyDown}
        // 영문 소문자는 같은 글꼴의 한글보다 낮게 찍혀(globals.css 참고) 위·아래 여백을 1px 다르게 둔다.
        className="flex items-center justify-between gap-1.5 whitespace-nowrap rounded-xl pt-[9px] pb-[11px] pl-3 pr-2.5 text-left text-xs sm:text-sm outline-none transition-colors"
        style={{ ...INPUT_STYLE, borderColor: open || focused ? INPUT_BORDER_FOCUS : INPUT_BORDER }}
        onFocus={() => setFocused(true)}
        onBlur={() => setFocused(false)}
      >
        {/* 가장 긴 도메인 자리를 미리 잡아 두어, 선택이 바뀌어도 칸 폭이 흔들리지 않는다 */}
        <span className="grid">
          {SCHOOL_DOMAINS.map((domain) => (
            <span
              key={domain}
              className="col-start-1 row-start-1"
              style={{ visibility: domain === value ? "visible" : "hidden" }}
              aria-hidden={domain !== value}
            >
              {domain}
            </span>
          ))}
        </span>
        <span
          className="flex flex-shrink-0 transition-transform duration-150"
          style={{ color: NAVY_MUTED, transform: open ? "rotate(180deg)" : undefined }}
        >
          <IconChevronDown />
        </span>
      </button>
      {open && (
        <ul
          id={listId}
          role="listbox"
          aria-label="이메일 도메인"
          className="absolute right-0 top-full z-20 mt-1.5 w-full overflow-hidden rounded-xl p-1"
          style={{
            background: "rgba(255,255,255,0.98)",
            border: `1px solid ${INPUT_BORDER}`,
            boxShadow: "0 8px 24px rgba(37,52,139,0.12)",
          }}
        >
          {SCHOOL_DOMAINS.map((domain, index) => {
            const selected = domain === value;
            return (
              <li
                key={domain}
                id={optionId(index)}
                role="option"
                aria-selected={selected}
                onPointerDown={(e) => e.preventDefault()} // 버튼 포커스 유지
                onPointerEnter={() => setActive(index)}
                onClick={() => choose(index)}
                className="cursor-pointer select-none whitespace-nowrap rounded-lg px-2.5 pt-[7px] pb-[9px] text-xs sm:text-sm transition-colors"
                style={{
                  color: NAVY,
                  fontWeight: selected ? 600 : 400,
                  background: selected
                    ? "rgba(37,52,139,0.08)"
                    : index === active
                      ? "rgba(37,52,139,0.04)"
                      : "transparent",
                }}
              >
                {domain}
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}

// 학교 이메일: 아이디 입력 + "@" + 학교 도메인 선택. 합친 주소는 부모가 만든다.
function SchoolEmailField({
  localPart,
  domain,
  onLocalPartChange,
  onDomainChange,
  error,
}: {
  localPart: string;
  domain: SchoolDomain;
  onLocalPartChange: (value: string) => void;
  onDomainChange: (value: SchoolDomain) => void;
  error?: string | null;
}) {
  const id = useId();
  const helpId = `${id}-help`;
  const errorId = `${id}-error`;
  const [helpOpen, setHelpOpen] = useState(false);
  const helpRef = useRef<HTMLDivElement>(null);

  // 안내 문구는 ?를 다시 누르거나, 바깥을 누르거나, Esc로 닫는다. iOS Safari는
  // 버튼을 눌러도 포커스를 주지 않아 onBlur만으로는 닫히지 않는다.
  useEffect(() => {
    if (!helpOpen) return;
    const closeOutside = (e: PointerEvent) => {
      if (!helpRef.current?.contains(e.target as Node)) setHelpOpen(false);
    };
    const closeOnEscape = (e: KeyboardEvent) => {
      if (e.key === "Escape") setHelpOpen(false);
    };
    document.addEventListener("pointerdown", closeOutside);
    document.addEventListener("keydown", closeOnEscape);
    return () => {
      document.removeEventListener("pointerdown", closeOutside);
      document.removeEventListener("keydown", closeOnEscape);
    };
  }, [helpOpen]);

  // "학번@pknu.ac.kr"을 통째로 붙여 넣어도 아이디·도메인으로 나눈다.
  const handleLocalPart = (raw: string) => {
    const at = raw.indexOf("@");
    if (at < 0) {
      onLocalPartChange(raw);
      return;
    }
    onLocalPartChange(raw.slice(0, at));
    const pasted = raw.slice(at + 1).trim().toLowerCase();
    if (isSchoolDomain(pasted)) onDomainChange(pasted);
  };

  return (
    <div className="flex flex-col gap-1.5">
      <div ref={helpRef} className="flex items-center gap-1">
        <label htmlFor={id} className="flex-shrink-0 whitespace-nowrap text-[11px] font-semibold" style={{ color: NAVY_MUTED }}>
          이메일
        </label>
        <button
          type="button"
          onClick={() => setHelpOpen((open) => !open)}
          aria-label="이메일 입력 안내"
          aria-expanded={helpOpen}
          aria-controls={helpId}
          className="flex h-3.5 w-3.5 flex-shrink-0 items-center justify-center rounded-full text-[9px] font-semibold leading-none transition-opacity hover:opacity-70"
          style={{ background: "rgba(37,52,139,0.08)", color: "rgba(37,52,139,0.4)" }}
        >
          ?
        </button>
        {helpOpen && (
          <span
            id={helpId}
            role="status"
            className="min-w-0 pl-0.5 text-[11px] leading-snug"
            style={{ color: NAVY_MUTED }}
          >
            학교 인증을 위해 부경대학교 웹메일을 입력해주세요
          </span>
        )}
      </div>
      <div className="flex items-center gap-1.5">
        <input
          id={id}
          type="text"
          inputMode="email"
          autoComplete="username"
          autoCapitalize="none"
          spellCheck={false}
          value={localPart}
          onChange={(e) => handleLocalPart(e.target.value)}
          placeholder="아이디"
          maxLength={64}
          aria-invalid={error ? true : undefined}
          aria-describedby={error ? errorId : undefined}
          className="min-w-0 flex-1 rounded-xl px-3 sm:px-3.5 py-2.5 text-xs sm:text-sm outline-none transition-colors placeholder:text-gray-400"
          style={INPUT_STYLE}
          onFocus={onFocusBorder}
          onBlur={onIdleBorder}
        />
        <span className="flex-shrink-0 text-xs sm:text-sm font-semibold" style={{ color: NAVY_MUTED }} aria-hidden>
          @
        </span>
        <DomainSelect value={domain} onChange={onDomainChange} />
      </div>
      {error && (
        <span id={errorId} role="alert" className="text-[11px] font-medium" style={{ color: ERROR_RED }}>
          {error}
        </span>
      )}
    </div>
  );
}

// 비밀번호 입력 + 우측 "보기/숨기기" 토글 (로그인·회원가입 공통)
function PasswordField({
  label,
  value,
  onChange,
  autoComplete,
  placeholder,
  error,
  children,
  describedBy,
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  autoComplete: string;
  placeholder: string;
  error?: string | null;
  /** 입력칸 아래에 보일 안내(예: 비밀번호 기준) */
  children?: ReactNode;
  /** 안내 요소 id — 화면 읽기 프로그램이 입력칸과 함께 읽는다 */
  describedBy?: string;
}) {
  const id = useId();
  const errorId = `${id}-error`;
  const [visible, setVisible] = useState(false);
  const idleBorder = error ? INPUT_BORDER_ERROR : INPUT_BORDER;

  return (
    <div className="flex flex-col gap-1.5">
      <label htmlFor={id} className="text-[11px] font-semibold" style={{ color: NAVY_MUTED }}>
        {label}
      </label>
      <div className="relative">
        <input
          id={id}
          type={visible ? "text" : "password"}
          autoComplete={autoComplete}
          value={value}
          onChange={(e) => onChange(e.target.value)}
          placeholder={placeholder}
          maxLength={PASSWORD_MAX}
          aria-invalid={error ? true : undefined}
          aria-describedby={[error ? errorId : null, describedBy ?? null].filter(Boolean).join(" ") || undefined}
          className="w-full rounded-xl py-2.5 pl-3.5 pr-10 text-xs sm:text-sm outline-none transition-colors placeholder:text-gray-400"
          style={{
            background: "rgba(255,255,255,0.85)",
            border: `1.5px solid ${idleBorder}`,
            color: NAVY,
            caretColor: NAVY,
          }}
          onFocus={(e) => { e.currentTarget.style.borderColor = error ? INPUT_BORDER_ERROR : INPUT_BORDER_FOCUS; }}
          onBlur={(e) => { e.currentTarget.style.borderColor = idleBorder; }}
        />
        <button
          type="button"
          onClick={() => setVisible((v) => !v)}
          aria-label={visible ? "비밀번호 숨기기" : "비밀번호 표시"}
          aria-pressed={visible}
          className="absolute inset-y-0 right-0 flex w-10 items-center justify-center rounded-r-xl transition-opacity hover:opacity-70"
          style={{ color: NAVY_MUTED }}
        >
          {visible ? <IconEyeOff /> : <IconEye />}
        </button>
      </div>
      {children}
      {error && (
        <span id={errorId} role="alert" className="text-[11px] font-medium" style={{ color: ERROR_RED }}>
          {error}
        </span>
      )}
    </div>
  );
}

type Mode = "signin" | "signup";

export default function AuthContent() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const { signInWithPassword, signUpWithPassword, user, loading } = useAuth();

  // ── 모드(?mode=signup|signin), 복귀 경로(?next=/foo) ─────────────────────────
  const initialMode: Mode = searchParams?.get("mode") === "signup" ? "signup" : "signin";
  const [mode, setMode] = useState<Mode>(initialMode);
  const nextPath = useMemo(() => safeNextPath(searchParams?.get("next")), [searchParams]);

  // ── 폼 상태 ────────────────────────────────────────────────────────────────
  // 로그인·회원가입 모두 아이디와 도메인을 따로 받아 하나의 주소로 합친다
  // (모든 계정이 학교 도메인이다 — DB 트리거가 가입을 막는다).
  const [emailLocalPart, setEmailLocalPart] = useState("");
  const [emailDomain, setEmailDomain] = useState<SchoolDomain>(SCHOOL_DOMAINS[0]);
  const email = emailLocalPart.trim() ? `${emailLocalPart.trim()}@${emailDomain}` : "";
  const [password, setPassword] = useState("");
  const [passwordConfirm, setPasswordConfirm] = useState("");
  const [confirmError, setConfirmError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [info, setInfo] = useState<string | null>(null);

  // ── 제출 가능 여부(버튼 색과 활성 상태) ───────────────────────────────────────
  // 로그인은 기존 계정(옛 6자 비밀번호 포함)이 들어올 수 있게 비밀번호 기준을 걸지 않는다.
  const localPart = emailLocalPart.trim();
  const localPartValid = LOCAL_PART_RE.test(localPart);
  const passwordValid = PASSWORD_RULES.every((rule) => rule.test(password));
  // 확인 칸은 비밀번호 길이만큼 쳤을 때부터 틀렸다고 알려 준다(입력 중에 깜빡이지 않게).
  const confirmMismatch =
    passwordConfirm.length >= password.length && passwordConfirm.length > 0 && passwordConfirm !== password;
  const canSubmit =
    mode === "signin"
      ? localPartValid && password.length > 0
      : localPartValid && passwordValid && passwordConfirm.length > 0 && passwordConfirm === password;
  const emailError =
    localPart && !localPartValid ? "아이디에는 영문, 숫자, . _ - + % 만 쓸 수 있어요." : null;
  const passwordRulesId = useId();

  // 콜백 라우트가 에러로 redirect 시킨 경우 ?error=<코드>를 정해 둔 문구로 표시
  // (주소에 적힌 글자를 그대로 띄우면 누구나 가짜 안내를 만들 수 있다).
  useEffect(() => {
    const message = authErrorMessage(searchParams?.get("error"));
    if (message) setError(message);
  }, [searchParams]);

  // 이미 로그인된 상태로 /auth 진입 → next 로 보내기
  useEffect(() => {
    if (!loading && user) {
      router.replace(nextPath);
    }
  }, [user, loading, nextPath, router]);

  // 모드 전환 시 에러/안내 메시지 초기화
  const switchMode = (next: Mode) => {
    if (next === mode) return;
    setMode(next);
    setError(null);
    setInfo(null);
    setPasswordConfirm("");
    setConfirmError(null);
  };

  // ── 이메일 폼 제출 ─────────────────────────────────────────────────────────
  const handleSubmit = async (e: FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    if (submitting) return;
    setError(null);
    setInfo(null);
    setConfirmError(null);

    if (!email || !password) {
      setError("이메일과 비밀번호를 모두 입력해 주세요.");
      return;
    }
    // 버튼이 꺼져 있어도 같은 검사를 한 번 더 한다(화면 상태를 우회한 제출 대비).
    if (!localPartValid || !SCHOOL_EMAIL_RE.test(email)) {
      setError("이메일 아이디를 확인해 주세요. 공백이나 @ 없이 아이디만 입력하면 됩니다.");
      return;
    }
    if (mode === "signup" && !passwordValid) {
      setError("비밀번호는 8자 이상, 영문과 숫자를 모두 포함해야 합니다.");
      return;
    }
    if (mode === "signup" && password !== passwordConfirm) {
      setConfirmError("비밀번호가 일치하지 않습니다.");
      return;
    }

    setSubmitting(true);
    try {
      const result =
        mode === "signin"
          ? await signInWithPassword(email, password)
          : await signUpWithPassword(email, password);

      if (!result.ok) {
        setError(result.message ?? "요청이 실패했습니다.");
        return;
      }

      if (mode === "signup" && result.emailConfirmationRequired) {
        setInfo(result.message ?? "확인 메일을 보냈습니다. 메일함을 확인해 주세요.");
        return;
      }

      router.replace(nextPath);
    } finally {
      setSubmitting(false);
    }
  };

  // ── 렌더 ──────────────────────────────────────────────────────────────────
  const isSignup = mode === "signup";

  return (
    // 카드 위쪽을 고정한다: 가운데 정렬이면 회원가입 칸이 펼쳐질 때 탭까지 위로 밀린다.
    // 위 여백은 회원가입 카드(약 560px)가 화면 가운데쯤 오도록 잡는다.
    <div
      className="bg-app min-h-[100dvh] flex items-start justify-center p-3 sm:p-4 lg:p-5"
      style={{ paddingTop: "max(12px, calc((100dvh - 560px) / 2))" }}
    >
      <div
        className="
          home-card w-full flex flex-col
          rounded-2xl sm:rounded-[24px]
        "
        style={{ maxWidth: "440px" }}
      >
        {/* ── Header ── */}
        <header
          className="flex items-center justify-between px-5 py-3 sm:px-6 sm:py-4"
          style={{ borderBottom: "1px solid rgba(37,52,139,0.1)" }}
        >
          <Link
            href="/"
            prefetch={false}
            className="flex items-center gap-1.5 px-2 py-1 rounded-lg transition-colors hover:bg-white/40"
            style={{ color: NAVY_MUTED, textDecoration: "none" }}
            aria-label="홈으로 돌아가기"
          >
            <IconArrowLeft />
            <span className="text-xs font-medium">홈</span>
          </Link>

          <div className="flex items-center gap-2 min-w-0">
            <Image
              src="/pukyong_logo.png"
              alt="부경대학교 로고"
              width={26}
              height={26}
              className="flex-shrink-0 object-contain"
              style={{ width: 26, height: 26 }}
            />
            <span className="text-xs sm:text-sm font-semibold truncate" style={{ color: NAVY }}>
              부경대학교
            </span>
          </div>
        </header>

        {/* ── Body ── */}
        <main className="flex flex-col gap-5 px-6 py-7 sm:px-8 sm:py-8">
          {/* 모드 탭 */}
          <div
            className="relative flex p-1 rounded-xl"
            style={{ background: "rgba(37,52,139,0.06)", border: "1px solid rgba(37,52,139,0.08)" }}
          >
            {/* 선택 표시(흰 칸)는 하나만 두고 좌우로 미끄러지게 한다. transform만 바꿔 가볍다. */}
            <span
              aria-hidden
              className="absolute inset-y-1 left-1 w-[calc(50%-4px)] rounded-lg bg-white transition-transform duration-300 ease-out motion-reduce:transition-none"
              style={{
                // 아래로만 지는 그림자는 아래 여백이 좁아 보이게 해서, 사방으로 고르게 아주 옅게 둔다.
                boxShadow: "0 0 0 1px rgba(37,52,139,0.04), 0 0 3px rgba(37,52,139,0.06)",
                transform: isSignup ? "translateX(100%)" : "translateX(0)",
              }}
            />
            {(["signin", "signup"] as const).map((m) => {
              const active = m === mode;
              return (
                <button
                  key={m}
                  type="button"
                  onClick={() => switchMode(m)}
                  aria-pressed={active}
                  className="relative flex-1 py-1.5 rounded-lg text-xs sm:text-[13px] font-semibold transition-colors duration-300 motion-reduce:transition-none"
                  style={{ color: active ? NAVY : NAVY_MUTED }}
                >
                  {m === "signin" ? "로그인" : "회원가입"}
                </button>
              );
            })}
          </div>

          {/* 이메일 / 비밀번호 폼 (학교 이메일만 가입 가능) */}
          <form className="flex flex-col gap-3" onSubmit={handleSubmit} noValidate>
            <SchoolEmailField
              localPart={emailLocalPart}
              domain={emailDomain}
              onLocalPartChange={setEmailLocalPart}
              onDomainChange={setEmailDomain}
              error={emailError}
            />

            <PasswordField
              label="비밀번호"
              value={password}
              onChange={(value) => { setPassword(value); setConfirmError(null); }}
              autoComplete={isSignup ? "new-password" : "current-password"}
              placeholder={isSignup ? "8자 이상, 영문·숫자 포함" : "비밀번호"}
              describedBy={isSignup ? passwordRulesId : undefined}
            >
              {/* PasswordField 안쪽 gap-1.5(6px) */}
              <Collapse open={isSignup} gap={6}>
                <PasswordRules password={password} id={passwordRulesId} />
              </Collapse>
            </PasswordField>

            {/* 폼의 gap-3(12px) */}
            <Collapse open={isSignup} gap={12}>
              <PasswordField
                label="비밀번호 확인"
                value={passwordConfirm}
                onChange={(value) => { setPasswordConfirm(value); setConfirmError(null); }}
                autoComplete="new-password"
                placeholder="비밀번호를 한 번 더 입력"
                error={confirmError ?? (confirmMismatch ? "비밀번호가 일치하지 않습니다." : null)}
              />
            </Collapse>

            {/* 에러 / 안내 메시지 */}
            {error && (
              <p
                className="text-xs leading-relaxed px-3 py-2 rounded-lg"
                style={{
                  background: "rgba(229,62,62,0.08)",
                  color: "#c53030",
                  border: "1px solid rgba(229,62,62,0.18)",
                }}
              >
                {error}
              </p>
            )}
            {info && (
              <p
                className="text-xs leading-relaxed px-3 py-2 rounded-lg"
                style={{
                  background: "rgba(37,52,139,0.07)",
                  color: NAVY,
                  border: "1px solid rgba(37,52,139,0.18)",
                }}
              >
                {info}
              </p>
            )}

            <button
              type="submit"
              // 조건을 채우기 전에는 꺼 둔다. 꺼진 제출 버튼은 Enter 제출도 막는다.
              disabled={submitting || !canSubmit}
              className="flex items-center justify-center gap-2 mt-1 py-2.5 rounded-xl text-xs sm:text-sm font-semibold text-white transition-[background-color,opacity] duration-200 enabled:hover:opacity-90 disabled:cursor-not-allowed motion-reduce:transition-none"
              style={{ background: canSubmit || submitting ? NAVY : "rgba(37,52,139,0.32)" }}
            >
              {submitting && <IconSpinner />}
              <span>{isSignup ? "회원가입" : "로그인"}</span>
            </button>
          </form>

          {/* 모드 전환 텍스트 */}
          <p className="text-xs text-center" style={{ color: NAVY_MUTED }}>
            {isSignup ? "이미 계정이 있으세요?" : "계정이 없으세요?"}{" "}
            <button
              type="button"
              onClick={() => switchMode(isSignup ? "signin" : "signup")}
              className="font-semibold underline underline-offset-2 hover:opacity-75 transition-opacity"
              style={{ color: NAVY, background: "transparent" }}
            >
              {isSignup ? "로그인" : "회원가입"}
            </button>
          </p>
        </main>

        {/* ── Footer ── */}
        <footer
          className="py-3 text-center text-[10px] sm:text-[11px]"
          style={{
            color: NAVY_MUTED,
            borderTop: "1px solid rgba(37,52,139,0.08)",
          }}
        >
          국립부경대학교 재학생만 이용할 수 있습니다
        </footer>
      </div>
    </div>
  );
}
