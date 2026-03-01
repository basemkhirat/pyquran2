import { defineConfig } from 'vitepress'

export default defineConfig({
  title: 'Quran Socket.IO Guide',
  description: 'Integration guide for mobile developers using the Quran voice recognition API',
  
  head: [
    ['meta', { name: 'theme-color', content: '#3eaf7c' }],
  ],

  themeConfig: {
    nav: [
      { text: 'Home', link: '/' },
      { text: 'Getting Started', link: '/getting-started/' },
      { text: 'Events', link: '/events/' },
      { text: 'Audio', link: '/audio/streaming' },
    ],

    sidebar: [
      {
        text: 'Introduction',
        items: [
          { text: 'Overview', link: '/' },
        ]
      },
      {
        text: 'Getting Started',
        items: [
          { text: 'Connection & Auth', link: '/getting-started/' },
        ]
      },
      {
        text: 'Socket Events',
        items: [
          { text: 'Overview', link: '/events/' },
          { text: 'Client Events', link: '/events/client-events' },
          { text: 'Server Events', link: '/events/server-events' },
        ]
      },
      {
        text: 'Audio',
        items: [
          { text: 'Format & Streaming', link: '/audio/streaming' },
        ]
      },
      {
        text: 'Platform Integration',
        items: [
          { text: 'iOS (Swift)', link: '/integration/ios' },
          { text: 'Android (Kotlin)', link: '/integration/android' },
        ]
      },
      // {
      //   text: 'Reference',
      //   items: [
      //     { text: 'Sequence Diagram', link: '/reference/sequence-diagram' },
      //   ]
      // },
    ],

    // socialLinks: [
    //   { icon: 'github', link: 'https://github.com/your-repo/quran2' }
    // ],

    footer: {
      message: 'Quran Voice Recognition API',
    },

    search: {
      provider: 'local'
    },
  }
})
