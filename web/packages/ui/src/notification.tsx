"use client";

import {
  createContext,
  useCallback,
  useContext,
  useRef,
  useState,
  type ReactNode,
} from "react";
import * as Toast from "@radix-ui/react-toast";
import {
  CheckCircleIcon,
  ExclamationCircleIcon,
} from "@heroicons/react/24/outline";
import { XMarkIcon } from "@heroicons/react/20/solid";

/**
 * Brand-styled toast notifications (Radix Toast under the hood). Wrap the app
 * once in `<NotificationProvider>`, then call `useNotification()` from any
 * client component to push a toast. Success uses the Signal check, errors a red
 * exclamation. Enter/exit + swipe animations live in the shared theme.css
 * (`.magpie-toast`, guarded by prefers-reduced-motion) since this stack has no
 * tailwind animate plugin.
 */
export interface NotifyOptions {
  title: string;
  body?: string;
  /** Red error styling vs. the default Signal success styling. */
  isError?: boolean;
  /** Auto-dismiss after this many ms (default 5000). */
  duration?: number;
}

interface ToastData {
  id: number;
  title: string;
  body?: string;
  isError: boolean;
  duration: number;
}

type NotifyFn = (opts: NotifyOptions) => void;

const NotificationContext = createContext<NotifyFn | null>(null);

export function useNotification(): NotifyFn {
  const ctx = useContext(NotificationContext);
  if (!ctx) {
    throw new Error(
      "useNotification must be used within a NotificationProvider",
    );
  }
  return ctx;
}

function ToastItem({
  data,
  dismiss,
}: {
  data: ToastData;
  dismiss: (id: number) => void;
}) {
  return (
    <Toast.Root
      duration={data.duration}
      onOpenChange={(open) => {
        if (!open) dismiss(data.id);
      }}
      className="magpie-toast pointer-events-auto w-full overflow-hidden rounded-lg border border-ink/10 bg-paper shadow-lg dark:border-paper/10 dark:bg-ink-soft"
    >
      <div className="flex items-start gap-3 p-4">
        <div className="shrink-0 pt-0.5">
          {data.isError ? (
            <ExclamationCircleIcon className="size-6 text-red-500 dark:text-red-400" />
          ) : (
            <CheckCircleIcon className="size-6 text-signal" />
          )}
        </div>
        <div className="min-w-0 flex-1 pt-0.5">
          <Toast.Title className="text-sm font-medium text-ink dark:text-paper">
            {data.title}
          </Toast.Title>
          {data.body && (
            <Toast.Description className="mt-1 text-sm text-ink-muted dark:text-paper/70">
              {data.body}
            </Toast.Description>
          )}
        </div>
        <Toast.Close className="-m-1 shrink-0 rounded-md p-1 text-ink-subtle hover:text-ink focus:outline-none focus-visible:ring-2 focus-visible:ring-signal dark:text-paper/55 dark:hover:text-paper">
          <span className="sr-only">Close</span>
          <XMarkIcon className="size-5" />
        </Toast.Close>
      </div>
    </Toast.Root>
  );
}

export function NotificationProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<ToastData[]>([]);
  const idCounter = useRef(0);

  const notify = useCallback<NotifyFn>(
    ({ title, body, isError = false, duration = 5000 }) => {
      const id = ++idCounter.current;
      setToasts((prev) => [...prev, { id, title, body, isError, duration }]);
    },
    [],
  );

  const dismiss = useCallback((id: number) => {
    setToasts((prev) => prev.filter((t) => t.id !== id));
  }, []);

  return (
    <Toast.Provider swipeDirection="right">
      <NotificationContext.Provider value={notify}>
        {children}
      </NotificationContext.Provider>
      {toasts.map((t) => (
        <ToastItem key={t.id} data={t} dismiss={dismiss} />
      ))}
      <Toast.Viewport className="fixed top-0 right-0 z-[100] flex w-full max-w-sm flex-col gap-2 p-4 sm:p-6" />
    </Toast.Provider>
  );
}
