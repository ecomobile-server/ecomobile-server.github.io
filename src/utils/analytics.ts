/**
 * Analytics Utility for Google Analytics 4
 * Note: PostHog is integrated separately via posthog.astro
 * Note: GA4 auto-tracks traffic source, campaign data, and page views
 */

declare global {
  interface PostHogFeatureFlags {
    capture?: (eventName: string, properties?: Record<string, any>) => void;
    onFeatureFlags?: (callback: () => void) => void;
    getFeatureFlag?: (key: string) => string | boolean | undefined;
    getFeatureFlagPayload?: (key: string) => any;
  }

  interface Window {
    gtag?: (...args: any[]) => void;
    dataLayer?: any[];
    fbq?: (...args: any[]) => void;
    _fbq?: any;
    posthog?: PostHogFeatureFlags;
  }
}

/**
 * Detect device type based on user agent and screen width
 */
export function getDeviceType(): 'mobile' | 'tablet' | 'desktop' {
  if (typeof window === 'undefined') return 'desktop';

  const width = window.innerWidth;
  const userAgent = navigator.userAgent.toLowerCase();

  // Check for mobile devices
  if (/mobile|android|iphone|ipod|blackberry|iemobile|opera mini/i.test(userAgent)) {
    return 'mobile';
  }

  // Check for tablets
  if (/tablet|ipad|playbook|silk/i.test(userAgent) || (width >= 768 && width < 1024)) {
    return 'tablet';
  }

  // Width-based detection
  if (width < 768) return 'mobile';
  if (width < 1024) return 'tablet';

  return 'desktop';
}

/**
 * Wait for gtag to be available
 */
function waitForGtag(callback: () => void, maxAttempts = 50) {
  let attempts = 0;
  const interval = setInterval(() => {
    attempts++;
    if (window.gtag) {
      clearInterval(interval);
      callback();
    } else if (attempts >= maxAttempts) {
      clearInterval(interval);
    }
  }, 100); // Check every 100ms
}

function waitForPostHog(callback: () => void, maxAttempts = 50) {
  let attempts = 0;
  const interval = setInterval(() => {
    attempts++;
    if (window.posthog) {
      clearInterval(interval);
      callback();
    } else if (attempts >= maxAttempts) {
      clearInterval(interval);
    }
  }, 100);
}

/**
 * Track event to GA4
 */
export function trackEvent(eventName: string, properties?: Record<string, any>) {
  // Add common properties
  const enrichedProperties = {
    ...properties,
    timestamp: new Date().toISOString(),
  };

  const sendEvent = () => {
    if (window.gtag) {
      try {
        window.gtag('event', eventName, enrichedProperties);
      } catch (e) {
        console.error('❌ GA4 tracking error:', e);
      }
    }
  };

  // If gtag is already available, send immediately
  if (window.gtag) {
    sendEvent();
  } else {
    // Otherwise wait for it to load
    waitForGtag(sendEvent);
  }

  // Send Facebook Pixel event if available
  if (window.fbq) {
    try {
      // Use 'trackCustom' for custom events, or specific standard events if they match FB's standard events
      window.fbq('trackCustom', eventName, enrichedProperties);
    } catch (e) {
      console.error('❌ FB tracking error:', e);
    }
  }
}

/**
 * Track event to PostHog when available
 */
export function trackPostHogEvent(
  eventName: string,
  properties?: Record<string, any>
) {
  const enrichedProperties = {
    ...properties,
    timestamp: new Date().toISOString(),
  };

  const sendEvent = () => {
    if (window.posthog?.capture) {
      try {
        window.posthog.capture(eventName, enrichedProperties);
      } catch (e) {
        console.error('❌ PostHog tracking error:', e);
      }
    }
  };

  if (window.posthog?.capture) {
    sendEvent();
  } else {
    waitForPostHog(sendEvent);
  }
}

/**
 * Track scroll depth
 */
let maxScrollDepth = 0;
let scrollTimeout: number | null = null;

export function initScrollTracking() {
  const trackScroll = () => {
    const windowHeight = window.innerHeight;
    const documentHeight = document.documentElement.scrollHeight;
    const scrollTop = window.scrollY || document.documentElement.scrollTop;
    const scrollPercent = Math.round((scrollTop / (documentHeight - windowHeight)) * 100);

    if (scrollPercent > maxScrollDepth) {
      maxScrollDepth = scrollPercent;

      // Debounce scroll tracking
      if (scrollTimeout) {
        clearTimeout(scrollTimeout);
      }

      scrollTimeout = window.setTimeout(() => {
        trackEvent('LandingPg_Scroll', {
          scroll_depth: scrollPercent,
          scroll_depth_percent: `${scrollPercent}%`,
        });
      }, 500);
    }
  };

  window.addEventListener('scroll', trackScroll, { passive: true });
}

/**
 * Track section visibility using Intersection Observer
 */
export function trackSectionView(sectionId: string, eventName: string) {
  const section = document.getElementById(sectionId);
  if (!section) return;

  const observer = new IntersectionObserver(
    (entries) => {
      entries.forEach((entry) => {
        if (entry.isIntersecting) {
          trackEvent(eventName, {
            section_id: sectionId,
          });
          // Track only once
          observer.disconnect();
        }
      });
    },
    {
      threshold: 0.3, // Trigger when 30% of section is visible
      rootMargin: '0px',
    }
  );

  observer.observe(section);
}

/**
 * Track CTA click with position
 */
export function trackCTAClick(
  position: string,
  properties?: Record<string, any>
) {
  trackEvent('LandingPg_CTA_Clicked', {
    cta_position: position,
    ...properties,
  });
}
