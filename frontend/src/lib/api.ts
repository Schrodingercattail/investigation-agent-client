import type { CreateTaskRequest, Task } from '@/types/task'

const API_BASE_URL = '/api'

// Custom error types for better error handling
export class ApiError extends Error {
  constructor(
    message: string,
    public statusCode: number,
    public endpoint: string,
    public originalError?: unknown
  ) {
    super(message)
    this.name = 'ApiError'
  }
}

export class NetworkError extends ApiError {
  constructor(endpoint: string, public originalError: Error) {
    super(
      `Network error while connecting to ${endpoint}: ${originalError.message}`,
      0,
      endpoint,
      originalError
    )
    this.name = 'NetworkError'
  }
}

export class ServerError extends ApiError {
  constructor(endpoint: string, message: string) {
    super(message, 500, endpoint)
    this.name = 'ServerError'
  }
}

export class RateLimitError extends ApiError {
  constructor(endpoint: string, message: string) {
    super(message, 429, endpoint)
    this.name = 'RateLimitError'
  }
}

export class ServiceUnavailableError extends ApiError {
  constructor(endpoint: string, message: string) {
    super(message, 503, endpoint)
    this.name = 'ServiceUnavailableError'
  }
}

export class AuthenticationError extends ApiError {
  constructor(endpoint: string, message: string) {
    super(message, 401, endpoint)
    this.name = 'AuthenticationError'
  }
}

export class NotFoundError extends ApiError {
  constructor(endpoint: string, resource: string) {
    super(`${resource} not found`, 404, endpoint)
    this.name = 'NotFoundError'
  }
}

export class ValidationError extends ApiError {
  constructor(endpoint: string, message: string) {
    super(message, 400, endpoint)
    this.name = 'ValidationError'
  }
}

// Helper function to parse error response
async function parseErrorResponse(response: Response, endpoint: string): Promise<Error> {
  try {
    const errorData = await response.json()
    const message = errorData.detail || errorData.message || 'Request failed'
    const endpointName = endpoint.replace(API_BASE_URL, '')

    // Map status codes to specific error types
    switch (response.status) {
      case 400:
        return new ValidationError(endpointName, message)
      case 401:
        return new AuthenticationError(endpointName, message)
      case 404:
        return new NotFoundError(endpointName, 'Task')
      case 429:
        return new RateLimitError(endpointName, message)
      case 500:
        return new ServerError(endpointName, message)
      case 503:
        return new ServiceUnavailableError(endpointName, message)
      case 504:
        return new ServiceUnavailableError(endpointName, 'Request timed out')
      default:
        return new ApiError(message, response.status, endpointName)
    }
  } catch {
    // If we can't parse the error, return a generic one
    return new ApiError(`Request failed with status ${response.status}`, response.status, endpoint)
  }
}

// Helper function to make fetch requests with error handling
async function fetchWithErrorHandling(url: string, options?: RequestInit): Promise<Response> {
  try {
    const response = await fetch(url, options)
    return response
  } catch (error) {
    if (error instanceof TypeError) {
      // Network errors (e.g., CORS, connection refused) throw TypeError
      throw new NetworkError(url, error as Error)
    }
    throw error
  }
}

export const api = {
  async createTask(request: CreateTaskRequest): Promise<Task> {
    const endpoint = `${API_BASE_URL}/tasks`
    const response = await fetchWithErrorHandling(endpoint, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify(request),
    })

    if (!response.ok) {
      throw await parseErrorResponse(response, endpoint)
    }

    return response.json()
  },

  async getTask(taskId: string): Promise<Task> {
    const endpoint = `${API_BASE_URL}/tasks/${taskId}`
    const response = await fetchWithErrorHandling(endpoint)

    if (!response.ok) {
      throw await parseErrorResponse(response, endpoint)
    }

    return response.json()
  },

  async runTask(taskId: string): Promise<Task> {
    const endpoint = `${API_BASE_URL}/tasks/${taskId}/run`
    const response = await fetchWithErrorHandling(endpoint, {
      method: 'POST',
    })

    if (!response.ok) {
      throw await parseErrorResponse(response, endpoint)
    }

    return response.json()
  },

  async regenerateTask(taskId: string): Promise<Task> {
    const endpoint = `${API_BASE_URL}/tasks/${taskId}/regenerate`
    const response = await fetchWithErrorHandling(endpoint, {
      method: 'POST',
    })

    if (!response.ok) {
      throw await parseErrorResponse(response, endpoint)
    }

    return response.json()
  },
}

// Export error types for use in components
export type {
  ApiError,
  NetworkError,
  ServerError,
  RateLimitError,
  ServiceUnavailableError,
  AuthenticationError,
  NotFoundError,
  ValidationError,
}
