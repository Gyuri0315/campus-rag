set search_path = public;

-- ── 학교 이메일 전용 가입: office.pknu.ac.kr 서브도메인 추가 허용 ────────────
-- 010_restrict_signup_email_domain.sql이 만든 트리거는 '%@pknu.ac.kr' /
-- '%@pukyong.ac.kr'로 끝나는 이메일만 허용했다. 이건 문자열 그대로 "@" 바로
-- 다음에 pknu.ac.kr이 와야 한다는 뜻이라, 실제로 쓰이고 있는
-- '...@office.pknu.ac.kr'(행정실 등 교직원 계정으로 추정)은 "@" 다음이
-- "office.pknu.ac.kr"이라 이 두 패턴 어디에도 안 걸려서 가입이 막혀 있었다.
-- 조건을 하나 더 추가해서 이 서브도메인도 허용한다.
create or replace function public.enforce_school_email_domain()
returns trigger
language plpgsql
security definer
as $$
begin
    if new.email is null or not (
        new.email ilike '%@pknu.ac.kr'
        or new.email ilike '%@pukyong.ac.kr'
        or new.email ilike '%@office.pknu.ac.kr'
    ) then
        raise exception 'school email required (@pknu.ac.kr, @pukyong.ac.kr, or @office.pknu.ac.kr)'
            using errcode = '23514';
    end if;
    return new;
end;
$$;
