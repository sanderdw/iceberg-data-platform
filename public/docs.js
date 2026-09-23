/* Swagger uses the same session and request protection as the workspace. */
window.ui = SwaggerUIBundle({
  url: '/openapi.json',
  dom_id: '#swagger-ui',
  deepLinking: true,
  supportedSubmitMethods: ['get', 'post', 'put', 'patch', 'delete', 'head', 'options'],
  requestInterceptor(request) {
    if (new URL(request.url, location.href).origin === location.origin) {
      request.credentials = 'same-origin';
      if (!['GET', 'HEAD'].includes((request.method || 'GET').toUpperCase())) {
        request.headers ||= {};
        request.headers['X-Portal-Request'] = '1';
        request.headers['Content-Type'] = 'application/json';
        if (request.body === undefined) request.body = '{}';
      }
    }
    return request;
  },
});
