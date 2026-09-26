// Stable entry point for admin pages: import { api, toast, ready } from '../scripts/admin-core'.
export { api, ApiError, adminHeaders, errorText, fieldErrors, friendlyMessage, goToSignIn, isMock, request } from './api';
export type { RequestOptions } from './api';
export { announce, busy, clearFieldErrors, confirmTwice, fill, h, openDrawer, showFieldErrors, toast, toastError } from './ui';
export { ready, me, login, logout, changePassword } from './session';
export { setUnread, refreshUnread } from './shell';
export { extendMock } from './mock';
export * from './format';
export type * from './types';
