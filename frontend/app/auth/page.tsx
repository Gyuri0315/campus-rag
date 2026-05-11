import { Suspense } from "react";
import AuthContent from "./AuthContent";

// AuthContent 가 useSearchParams() 를 사용하므로 Suspense 경계가 필수다.
// (Next.js 16 클라이언트 컴포넌트에서 useSearchParams 를 쓸 때의 빌드 요구사항)
export default function AuthPage() {
  return (
    <Suspense fallback={null}>
      <AuthContent />
    </Suspense>
  );
}
