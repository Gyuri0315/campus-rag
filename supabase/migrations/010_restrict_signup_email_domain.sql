set search_path = public;

-- ── 학교 이메일 전용 가입 ──────────────────────────────────────────────────
-- 부경대 재학생 메일은 학번@pknu.ac.kr 또는 학번@pukyong.ac.kr 형태만 존재하므로,
-- 이 두 도메인이 아닌 이메일로는 auth.users 에 새 계정이 생성되지 못하게 막는다.
-- (프론트에서 카카오/구글 OAuth는 제거했지만, 이 트리거가 실제 강제 지점이다 —
--  클라이언트 검증은 우회 가능하므로 신뢰하지 않는다.)
create or replace function public.enforce_school_email_domain()
returns trigger
language plpgsql
security definer
as $$
begin
    if new.email is null or not (
        new.email ilike '%@pknu.ac.kr' or new.email ilike '%@pukyong.ac.kr'
    ) then
        raise exception 'school email required (@pknu.ac.kr or @pukyong.ac.kr)'
            using errcode = '23514';
    end if;
    return new;
end;
$$;

drop trigger if exists enforce_school_email_domain on auth.users;
create trigger enforce_school_email_domain
before insert on auth.users
for each row execute function public.enforce_school_email_domain();
