export default {
    async fetch(request) {
      const targetOrigin = "https://basemkhirat-65032--memorize-quran-serve.modal.run";
      const url = new URL(request.url);
      const targetUrl = targetOrigin + url.pathname + url.search;
  
      const targetRequest = new Request(targetUrl, request);
      targetRequest.headers.set("Host", new URL(targetOrigin).hostname);
  
      const response = await fetch(targetRequest);
  
      // Return response as-is, with your own headers if you want
      return new Response(response.body, response);
    }
  };
  
  