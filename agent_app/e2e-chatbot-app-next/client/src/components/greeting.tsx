import { motion } from 'framer-motion';
import { useAppConfig } from '@/contexts/AppConfigContext';

const DEFAULT_APP_LOGO_URL =
  'https://raw.githubusercontent.com/databricks-solutions/dbx-unifiedchat/main/docs/logos/dbx-unifiedchat-logo-pacman-eating-data.png';

export const Greeting = () => {
  const { appLogoUrl } = useAppConfig();
  const logoUrl = appLogoUrl || DEFAULT_APP_LOGO_URL;

  return (
    <div
      key="overview"
      className="mx-auto mt-4 flex size-full max-w-3xl flex-col items-center justify-center px-4 md:mt-16 md:px-8"
    >
      {/* Logo */}
      <motion.div
        initial={{ opacity: 0, scale: 0.7 }}
        animate={{ opacity: 1, scale: 1 }}
        exit={{ opacity: 0, scale: 0.7 }}
        transition={{ delay: 0.2, type: 'spring', stiffness: 200, damping: 15 }}
        className="mb-8"
      >
        <img
          src={logoUrl}
          alt="dbx-unifiedchat logo"
          className="h-36 w-auto drop-shadow-lg md:h-44"
        />
      </motion.div>

      {/* Colorful gradient headline */}
      <motion.div
        initial={{ opacity: 0, y: 10 }}
        animate={{ opacity: 1, y: 0 }}
        exit={{ opacity: 0, y: 10 }}
        transition={{ delay: 0.5 }}
        className="font-bold text-2xl md:text-3xl bg-gradient-to-r from-orange-500 via-red-500 to-pink-500 bg-clip-text text-transparent"
      >
        Hello there! 👋
      </motion.div>

      {/* Subtitle with colorful accent */}
      <motion.div
        initial={{ opacity: 0, y: 10 }}
        animate={{ opacity: 1, y: 0 }}
        exit={{ opacity: 0, y: 10 }}
        transition={{ delay: 0.6 }}
        className="mt-2 text-xl md:text-2xl text-zinc-500"
      >
        How can I help you today?
      </motion.div>

      {/* Colorful tag pills */}
      <motion.div
        initial={{ opacity: 0, y: 10 }}
        animate={{ opacity: 1, y: 0 }}
        exit={{ opacity: 0, y: 10 }}
        transition={{ delay: 0.75 }}
        className="mt-6 flex flex-wrap justify-center gap-2"
      >
        {[
          { label: '⚡ Fast Queries', color: 'bg-orange-100 text-orange-700 border border-orange-300' },
          { label: '🤖 AI-Powered', color: 'bg-purple-100 text-purple-700 border border-purple-300' },
          { label: '📊 Databricks Native', color: 'bg-red-100 text-red-700 border border-red-300' },
          { label: '🔗 Multi-Agent', color: 'bg-blue-100 text-blue-700 border border-blue-300' },
        ].map(({ label, color }) => (
          <span
            key={label}
            className={`rounded-full px-3 py-1 text-xs font-medium ${color}`}
          >
            {label}
          </span>
        ))}
      </motion.div>
    </div>
  );
};
