import * as React from "react";
import {
  Body,
  Container,
  Font,
  Head,
  Heading,
  Hr,
  Html,
  Img,
  Preview,
  Tailwind,
  Text,
} from "@react-email/components";
import { emailTailwindConfig, poppins, WORDMARK_URL } from "../brand";

/** Shared chrome for every OpenMagpie email: wordmark, heading, body slot,
 * footer. Templates supply the preview line, heading, and content. */
export function EmailLayout({
  preview,
  heading,
  children,
}: {
  preview: string;
  heading: string;
  children: React.ReactNode;
}) {
  return (
    <Html>
      <Preview>{preview}</Preview>
      <Tailwind config={emailTailwindConfig}>
        <Head>
          <meta name="color-scheme" content="light" />
          <Font {...poppins} />
        </Head>
        <Body className="bg-paper-soft py-8 font-sans">
          <Container className="mx-auto max-w-[480px] rounded-2xl border border-black/5 bg-white p-10">
            {/* OpenMagpie wordmark (emblem + name). alt carries the name so it
                still reads if the client blocks images. */}
            <Img
              src={WORDMARK_URL}
              alt="OpenMagpie"
              width="148"
              height="28"
              className="mb-6"
              style={{ display: "block" }}
            />
            <Heading className="mt-0 mb-4 text-2xl font-bold text-ink">{heading}</Heading>
            {children}
            <Hr className="my-8 border-black/10" />
            <Text className="m-0 text-xs text-ink-muted">
              OpenMagpie | Open-source, self-hostable social listening.
            </Text>
          </Container>
        </Body>
      </Tailwind>
    </Html>
  );
}
