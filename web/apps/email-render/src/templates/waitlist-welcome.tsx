import * as React from "react";
import { Text } from "@react-email/components";
import { SITE_URL } from "../brand";
import { EmailLayout } from "./_layout";

interface WaitlistWelcomeProps {
  siteUrl?: string;
}

export const WaitlistWelcome: React.FC<WaitlistWelcomeProps> = ({
  siteUrl = SITE_URL,
}) => (
  <EmailLayout preview="You're on the OpenMagpie waitlist" heading="You're on the list!">
    <Text className="text-base leading-relaxed text-ink-muted">
      Thanks for joining the OpenMagpie waitlist. We&apos;ll email you when early access to
      the hosted version opens.
    </Text>
    <Text className="text-base leading-relaxed text-ink-muted">
      Can&apos;t wait? OpenMagpie is open source and free to self-host today:{" "}
      <a href={siteUrl} className="text-signal">
        {siteUrl}
      </a>
    </Text>
  </EmailLayout>
);

export default WaitlistWelcome;
